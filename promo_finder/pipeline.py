"""Promo finder pipeline.

  parse (code) -> per-episode candidates (LLM) -> resolve + validate + auto-fix (code)
  -> cross-episode montages (LLM) -> judge scores (LLM) -> rank, dedupe, posting plan (code)
"""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Callable

from . import prompts
from .llm import LLM
from .parser import Episode, Event, fmt_time, render_for_llm

MIN_LEN, MAX_LEN = 20.0, 60.0
TOLERANCE = 3.0      # accept 17-63s as "borderline", flagged
HOOK_WINDOW = 3.0
SNAP_MAX = 3.0       # max seconds we move a cut to avoid chopping a line

WEIGHTS = {"hook": 0.30, "cliffhanger": 0.25, "standalone": 0.20, "appeal": 0.15, "self": 0.10}


@dataclass
class Segment:
    episode: int
    start: float
    end: float
    start_event: str
    end_event: str

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 2)

    def to_dict(self):
        return {"episode": self.episode, "start": fmt_time(self.start), "end": fmt_time(self.end),
                "start_sec": round(self.start, 2), "end_sec": round(self.end, 2),
                "duration_sec": self.duration, "start_event": self.start_event, "end_event": self.end_event}


@dataclass
class Candidate:
    id: str
    kind: str                     # single | montage
    title: str
    segments: list[Segment]
    hook: str
    cliffhanger: str
    why: str
    standalone_gap: str = ""
    tropes: list[str] = field(default_factory=list)
    self_score: float = 5
    hook_event: str | None = None
    cliffhanger_event: str | None = None
    fixes: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    valid: bool = True
    scores: dict = field(default_factory=dict)
    final_score: float = 0.0
    clip_text: str = ""
    first_line_at: float | None = None   # seconds until the first spoken line (code-measured)

    @property
    def duration(self) -> float:
        return round(sum(s.duration for s in self.segments), 2)

    @property
    def episodes(self) -> list[int]:
        return sorted({s.episode for s in self.segments})

    def to_dict(self):
        d = asdict(self)
        d["segments"] = [s.to_dict() for s in self.segments]
        d["duration_sec"] = self.duration
        d["episodes"] = self.episodes
        return d


# --------------------------------------------------------------------------- helpers
def _events_in(ep: Episode, start: float, end: float) -> list[Event]:
    return [e for e in ep.events if e.start >= start - 0.01 and e.start < end - 0.01]


def _snap_segment(ep: Episode, seg: Segment, fixes: list[str]) -> None:
    """Don't start or end in the middle of a spoken line."""
    for e in ep.events:
        if e.kind != "DIALOGUE":
            continue
        if e.start < seg.end - 0.05 and seg.end + 0.05 < e.end and e.end - seg.end <= SNAP_MAX:
            fixes.append(f"ep{seg.episode}: extended end {fmt_time(seg.end)}->{fmt_time(e.end)} to finish line")
            seg.end, seg.end_event = e.end, e.local_id
        if e.start < seg.start - 0.05 and seg.start + 0.3 < e.end and seg.start - e.start <= SNAP_MAX:
            fixes.append(f"ep{seg.episode}: moved start {fmt_time(seg.start)}->{fmt_time(e.start)} to catch full line")
            seg.start, seg.start_event = e.start, e.local_id


def _clip_text(eps: dict[int, Episode], segments: list[Segment]) -> str:
    """Readable clip transcript with times relative to the clip start (used by judge + report)."""
    out, offset = [], 0.0
    for i, s in enumerate(segments):
        ep = eps[s.episode]
        if len(segments) > 1:
            out.append(f"--- segment {i + 1}: episode {s.episode} {fmt_time(s.start)}-{fmt_time(s.end)} ---")
        for e in _events_in(ep, s.start, s.end):
            rel = offset + (e.start - s.start)
            if e.kind == "CAMERA":
                if rel > HOOK_WINDOW:  # camera lines only for the opening, where the visual IS the hook
                    continue
                out.append(f"[{rel:5.1f}s] (camera) {e.text[:110]}")
            elif e.kind == "DIALOGUE":
                emo = f" ({e.emotion})" if e.emotion else ""
                out.append(f'[{rel:5.1f}s] {e.speaker}{emo}: "{e.text}"')
            else:
                out.append(f"[{rel:5.1f}s] ({e.kind.lower()}) {e.text}")
        offset += s.duration
    return "\n".join(out)


def validate(c: Candidate, eps: dict[int, Episode]) -> Candidate:
    # 1. snap cuts so lines aren't chopped
    for s in c.segments:
        _snap_segment(eps[s.episode], s, c.fixes)

    # 2. hook must land in the first 3 seconds
    if c.hook_event and c.kind == "single":
        first = c.segments[0]
        hev = eps[first.episode].by_local().get(c.hook_event)
        if hev and first.start <= hev.start < first.end:
            if hev.start - first.start > HOOK_WINDOW:
                new_start, new_id = hev.start, hev.local_id
                # keep the camera shot that introduces the hook if it starts just before
                for e in eps[first.episode].events:
                    if e.kind == "CAMERA" and 0 <= hev.start - e.start <= 1.0:
                        new_start, new_id = e.start, e.local_id
                        break
                c.fixes.append(f"trimmed start {fmt_time(first.start)}->{fmt_time(new_start)} so hook is in first 3s")
                first.start, first.start_event = new_start, new_id
        elif hev:
            c.issues.append("hook event is outside the clip")

    # 3. structural checks
    for s in c.segments:
        if s.end <= s.start:
            c.issues.append(f"empty/negative segment in ep{s.episode}")
    if c.kind == "single":  # single-episode promos must play in story order
        for a, b in zip(c.segments, c.segments[1:]):
            if a.episode == b.episode and b.start < a.end - 0.05:
                c.issues.append("segments overlap or are out of order")
    else:  # montages may reorder moments for drama, but must not repeat footage
        for i, a in enumerate(c.segments):
            for b in c.segments[i + 1:]:
                if a.episode == b.episode and min(a.end, b.end) - max(a.start, b.start) > 0.05:
                    c.issues.append("montage segments overlap")

    d = c.duration
    if d < MIN_LEN - TOLERANCE or d > MAX_LEN + TOLERANCE:
        c.issues.append(f"duration {d:.1f}s outside 20-60s")
    elif not (MIN_LEN <= d <= MAX_LEN):
        c.fixes.append(f"borderline duration {d:.1f}s (accepted within ±{TOLERANCE:.0f}s tolerance)")

    blocking = [i for i in c.issues if "duration" in i or "empty" in i or "overlap" in i]
    c.valid = not blocking
    c.clip_text = _clip_text(eps, c.segments)
    first = c.segments[0]
    spoken = [e for e in _events_in(eps[first.episode], first.start, first.end) if e.kind == "DIALOGUE"]
    c.first_line_at = round(spoken[0].start - first.start, 1) if spoken else None
    if c.first_line_at is None or c.first_line_at > HOOK_WINDOW:
        c.fixes.append(f"warning: no spoken line in first 3s (first line at {c.first_line_at}s) - hook is visual only")
    return c


# --------------------------------------------------------------------------- stages
def find_in_batch(llm: LLM, batch: list[Episode], all_eps: list[Episode], title: str) -> dict:
    """One LLM call for several consecutive episodes (fewer calls = fits free-tier daily quota,
    lower cost at scale). Story-so-far = loglines of the 6 episodes before the batch."""
    first = batch[0].number
    prev = [e for e in all_eps if e.number < first][-6:]
    story = "\n".join(f"Ep {e.number}: {e.logline}" for e in prev) or "(this batch starts the series)"
    block = "\n".join(prompts.EPISODE_BLOCK.format(
        number=ep.number, duration=fmt_time(ep.duration), logline=ep.logline,
        transcript=render_for_llm(ep)) for ep in batch)
    user = prompts.EPISODE_USER.format(title=title, story_so_far=story, episodes_block=block)
    return llm.json(prompts.EPISODE_SYSTEM, user)


def _ep_of(event_id) -> int | None:
    m = re.match(r"^\s*(\d+)-E\d+", str(event_id or ""))
    return int(m.group(1)) if m else None


def _local(event_id) -> str:
    """'07-E031' -> 'E031' ; 'E031' -> 'E031'"""
    m = re.search(r"(E\d+)\s*$", str(event_id or ""))
    return m.group(1) if m else str(event_id)


def split_batch(raw, numbers: list[int]) -> dict[int, dict]:
    """Route each promo / key moment to its episode using the id prefix (falls back to the
    'episode' field). Tolerates the shapes models actually return."""
    out = {n: {"promos": [], "key_moments": []} for n in numbers}
    if isinstance(raw, dict) and "episodes" in raw:
        entries = raw["episodes"] or []
    elif isinstance(raw, list) and raw and isinstance(raw[0], dict) and "episodes" in raw[0]:
        entries = [e for x in raw for e in (x.get("episodes") or [])]
    else:
        entries = [_as_obj(raw, "promos")]
    for ent in entries:
        if not isinstance(ent, dict):
            continue
        default = _num(ent.get("episode"), None)
        for key in ("promos", "key_moments"):
            for item in ent.get(key, []) or []:
                if not isinstance(item, dict):
                    continue
                ref = (item.get("segments") or [{}])[0].get("start_event") if key == "promos" else item.get("start_event")
                ep = _ep_of(ref) or (int(default) if default else None)
                if ep in out:
                    out[ep][key].append(item)
    return out


def candidates_from_episode(raw: dict, ep: Episode, eps: dict[int, Episode]) -> tuple[list[Candidate], list[dict]]:
    by_id = ep.by_local()
    raw = _as_obj(raw, "promos")
    cands, moments = [], []
    for i, p in enumerate(raw.get("promos", []) or []):
        segs, issues = [], []
        for s in p.get("segments", []) or []:
            a, b = by_id.get(_local(s.get("start_event"))), by_id.get(_local(s.get("end_event")))
            if not a or not b:
                issues.append(f"unknown event id in {s}")
                continue
            if b.end < a.start:
                a, b = b, a
            segs.append(Segment(ep.number, a.start, max(b.end, a.end), a.local_id, b.local_id))
        c = Candidate(
            id=f"ep{ep.number:02d}-p{i + 1}", kind="single", title=p.get("title", ""), segments=segs,
            hook=p.get("hook", ""), cliffhanger=p.get("cliffhanger", ""), why=p.get("why", ""),
            standalone_gap=p.get("standalone_gap", ""), tropes=p.get("tropes", []) or [],
            self_score=_num(p.get("self_score"), 5), hook_event=_local(p.get("hook_event")) if p.get("hook_event") else None,
            cliffhanger_event=_local(p.get("cliffhanger_event")) if p.get("cliffhanger_event") else None, issues=issues)
        if not segs:
            c.valid = False
            c.issues.append("no usable segments")
            cands.append(c)
            continue
        cands.append(validate(c, eps))

    for j, m in enumerate(raw.get("key_moments", []) or []):
        a, b = by_id.get(_local(m.get("start_event"))), by_id.get(_local(m.get("end_event")))
        if not a or not b:
            continue
        if b.end < a.start:
            a, b = b, a
        moments.append({"id": f"m{ep.number:02d}_{j + 1}", "episode": ep.number, "start": a.start,
                        "end": max(a.end, b.end), "start_event": a.local_id, "end_event": b.local_id,
                        "type": m.get("type", ""), "description": m.get("description", "")})
    return cands, moments


def build_montages(llm: LLM, moments: list[dict], all_eps: list[Episode], eps: dict[int, Episode],
                   title: str) -> tuple[dict, list[Candidate]]:
    if not moments:
        return {}, []
    cap = int(os.getenv("MONTAGE_MAX_MOMENTS", "0") or 0)
    if cap and len(moments) > cap:
        # small-context providers: keep a capped set, round-robin across episodes, strongest types first
        prio = {"hook": 0, "reveal": 1, "cliffhanger": 2, "emotional": 3}
        by_ep: dict[int, list[dict]] = {}
        for m in moments:
            by_ep.setdefault(m["episode"], []).append(m)
        queues = [sorted(v, key=lambda m: prio.get(str(m["type"]).lower(), 9)) for _, v in sorted(by_ep.items())]
        picked: list[dict] = []
        while len(picked) < cap and any(queues):
            for q in queues:
                if q and len(picked) < cap:
                    picked.append(q.pop(0))
        moments = sorted(picked, key=lambda m: (m["episode"], m["start"]))
    lchars = int(os.getenv("MONTAGE_LOGLINE_CHARS", "400"))
    lines = []
    for m in moments:
        ev = _events_in(eps[m["episode"]], m["start"], m["end"])
        said = " / ".join(f'{e.speaker}: "{e.text}"' for e in ev if e.kind == "DIALOGUE")[:220]
        lines.append(f'{m["id"]} | ep{m["episode"]} | {m["end"] - m["start"]:.1f}s | {m["type"]} | '
                     f'{m["description"]} | {said}')
    user = prompts.MONTAGE_USER.format(
        title=title, loglines="\n".join(f"Ep {e.number}: {e.logline[:lchars]}" for e in all_eps),
        moments="\n".join(lines))
    raw = llm.json(prompts.MONTAGE_SYSTEM, user)
    raw = _as_obj(raw, "promos")
    mom = {m["id"]: m for m in moments}
    out = []
    for i, p in enumerate(raw.get("promos", []) or []):
        ids = [x for x in p.get("moment_ids", []) if x in mom]
        segs = [Segment(mom[x]["episode"], mom[x]["start"], mom[x]["end"], mom[x]["start_event"], mom[x]["end_event"])
                for x in ids]
        c = Candidate(id=f"montage-{i + 1}", kind="montage", title=p.get("title", ""), segments=segs,
                      hook=p.get("hook", ""), cliffhanger=p.get("cliffhanger", ""), why=p.get("why", ""),
                      standalone_gap=p.get("standalone_gap", ""), tropes=p.get("tropes", []) or [],
                      self_score=_num(p.get("self_score"), 5))
        bad = [x for x in p.get("moment_ids", []) if x not in mom]
        if bad:
            c.issues.append(f"model referenced unknown moments {bad}")
        if len(segs) < 2:
            c.valid = False
            c.issues.append("montage needs 2+ valid moments")
            c.clip_text = _clip_text(eps, segs) if segs else ""
            out.append(c)
            continue
        out.append(validate(c, eps))
    return raw, out


def judge(llm: LLM, cands: list[Candidate], title: str, batch: int | None = None) -> list[dict]:
    batch = batch or int(os.getenv("JUDGE_BATCH", "20"))
    raws = []
    for i in range(0, len(cands), batch):
        chunk = cands[i:i + batch]
        text = "\n\n".join(f"### id={c.id} ({c.duration:.0f}s, episodes {c.episodes})\n{c.clip_text}" for c in chunk)
        raw = llm.json(prompts.JUDGE_SYSTEM, prompts.JUDGE_USER.format(title=title, candidates=text), temperature=0.1)
        raws.append(raw)
        got = {str(s.get("id")): s for s in _as_obj(raw, "scores").get("scores", []) if isinstance(s, dict)}
        for c in chunk:
            if c.id in got:
                c.scores = {k: got[c.id].get(k) for k in ("hook", "cliffhanger", "standalone", "appeal", "spoiler", "verdict")}
            else:
                c.issues.append("judge did not score this candidate")
    return raws


def score(c: Candidate) -> float:
    s = c.scores or {}
    norm = lambda v: (_num(v, 3) - 1) / 4  # 1..5 -> 0..1
    total = (WEIGHTS["hook"] * norm(s.get("hook")) + WEIGHTS["cliffhanger"] * norm(s.get("cliffhanger"))
             + WEIGHTS["standalone"] * norm(s.get("standalone")) + WEIGHTS["appeal"] * norm(s.get("appeal"))
             + WEIGHTS["self"] * (_num(c.self_score, 5) - 1) / 9)
    total -= 0.04 * max(0, _num(s.get("spoiler"), 1) - 2)       # big spoilers hurt
    if not (25 <= c.duration <= 50):
        total -= 0.03                                            # outside the sweet spot
    if c.standalone_gap:
        total -= 0.02
    if c.first_line_at is None or c.first_line_at > HOOK_WINDOW:
        total -= 0.05   # judge reads text and overrates purely visual openings
    return round(100 * total, 1)


def _overlap(a: Candidate, b: Candidate) -> float:
    inter = 0.0
    for x in a.segments:
        for y in b.segments:
            if x.episode == y.episode:
                inter += max(0.0, min(x.end, y.end) - max(x.start, y.start))
    return inter / max(1e-6, min(a.duration, b.duration))


def rank(cands: list[Candidate]) -> list[Candidate]:
    for c in cands:
        c.final_score = score(c)
    ordered = sorted([c for c in cands if c.valid], key=lambda c: (-c.final_score, min(c.episodes)))
    kept: list[Candidate] = []
    for c in ordered:
        dup = next((k for k in kept if _overlap(c, k) > 0.5), None)
        if dup:
            c.issues.append(f"near-duplicate of higher-ranked {dup.id}")
            continue
        kept.append(c)
    return kept


def posting_plan(ranked: list[Candidate], n: int = 10, per_episode: int = 2) -> list[Candidate]:
    """Top-n by score, max `per_episode` clips per episode (so the first week of ads tests
    different story beats) and no clip the judge rated spoiler >= 4."""
    plan, count = [], {}
    for c in ranked:
        if _num((c.scores or {}).get("spoiler"), 1) >= 4:
            continue  # gives away a major twist: keep it ranked, but don't lead the campaign with it
        key = c.episodes[0] if c.kind == "single" else "montage"
        if count.get(key, 0) >= per_episode:
            continue
        plan.append(c)
        count[key] = count.get(key, 0) + 1
        if len(plan) == n:
            break
    return plan


def _as_obj(raw, key: str) -> dict:
    """Models sometimes wrap the object in a list or return the bare list. Normalise to {key: [...], ...}."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        if raw and all(isinstance(x, dict) and key in x for x in raw):
            merged: dict = {}
            for x in raw:
                for k, v in x.items():
                    merged.setdefault(k, [])
                    merged[k] += v if isinstance(v, list) else [v]
            return merged
        return {key: [x for x in raw if isinstance(x, dict)]}
    return {}


def _num(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- orchestrator
def parse_episode_spec(spec: str) -> set[int]:
    """'7' -> {7};  '5-10,12' -> {5..10, 12}"""
    out: set[int] = set()
    for part in str(spec).replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.update(range(int(a), int(b) + 1))
        else:
            out.add(int(part))
    return out


def run(episodes: list[Episode], title: str, out_dir: Path, llm: LLM | None = None,
        max_episodes: int | None = None, only: set[int] | None = None,
        batch_size: int | None = None, log: Callable[[str], None] = print) -> dict:
    """`episodes` should be the WHOLE show (used for story-so-far context);
    `only` / `max_episodes` choose which episodes to search for promos."""
    llm = llm or LLM()
    t0 = time.time()
    out_dir = Path(out_dir)
    (out_dir / "raw_llm").mkdir(parents=True, exist_ok=True)
    eps = {e.number: e for e in episodes}
    todo = [e for e in episodes if e.number in only] if only else episodes
    todo = todo[:max_episodes] if max_episodes else todo
    if not todo:
        raise ValueError(f"No matching episodes. Available: {min(eps)}-{max(eps)}")

    all_cands, all_moments = [], []
    bsize = max(1, int(batch_size or os.getenv("BATCH_EPISODES", "5")))
    batches = [todo[i:i + bsize] for i in range(0, len(todo), bsize)]
    for batch in batches:
        nums = [e.number for e in batch]
        label = f"ep{nums[0]:02d}-{nums[-1]:02d}" if len(nums) > 1 else f"ep{nums[0]:02d}"
        log(f"Episodes {nums}: finding promos ({sum(len(e.events) for e in batch)} lines)")
        try:
            raw = find_in_batch(llm, batch, episodes, title)
        except Exception as e:
            log(f"  !! batch {nums} failed: {e}")
            (out_dir / "raw_llm" / f"batch_{label}.error.txt").write_text(str(e), encoding="utf-8")
            continue
        _dump(out_dir / "raw_llm" / f"batch_{label}.json", raw)
        try:
            per_ep = split_batch(raw, nums)
        except Exception as e:
            log(f"  !! could not read batch {nums} response: {e!r}")
            continue
        for ep in batch:
            try:
                cands, moments = candidates_from_episode(per_ep[ep.number], ep, eps)
            except Exception as e:  # unexpected response shape: log it, keep going
                log(f"  !! could not read episode {ep.number}: {e!r}")
                continue
            all_cands += cands
            all_moments += moments
            log(f"  ep{ep.number}: {sum(c.valid for c in cands)}/{len(cands)} valid promos, {len(moments)} key moments")

    trimmed = 0
    for i, c in enumerate(all_cands):
        t = trim_long(c, eps) if not c.valid else None
        if t and t.valid:
            all_cands[i] = t
            trimmed += 1
    if trimmed:
        log(f"Code-trimmed {trimmed} too-long promos (hook + cliffhanger, middle cut)")
    if any(not c.valid for c in all_cands):
        log("Repairing promos that broke the 20-60s rule")
        try:
            rraw, _ = repair_lengths(llm, all_cands, eps, log)
            if rraw:
                _dump(out_dir / "raw_llm" / "repair.json", rraw)
        except Exception as e:
            log(f"  !! repair step failed: {e}")

    log(f"Building cross-episode montages from {len(all_moments)} key moments")
    try:
        mraw, montages = build_montages(llm, all_moments, episodes, eps, title)
        _dump(out_dir / "raw_llm" / "montages.json", mraw)
        all_cands += montages
    except Exception as e:
        log(f"  !! montage step failed: {e}")

    valid = [c for c in all_cands if c.valid]
    log(f"Judging {len(valid)} valid candidates")
    jraw = judge(llm, valid, title)
    _dump(out_dir / "raw_llm" / "judge.json", jraw)

    ranked = rank(all_cands)
    plan = posting_plan(ranked)
    meta = {"title": title, "model": llm.name, "models_exhausted": getattr(llm, "exhausted", []), "episodes_processed": len(todo), "batch_size": bsize,
            "llm_calls": llm.calls, "models_used": getattr(llm, "models_used", {}), "cache_hits": llm.cache_hits, "seconds": round(time.time() - t0, 1),
            "candidates_total": len(all_cands), "candidates_valid": len(valid), "ranked": len(ranked)}

    result = {"meta": meta,
              "posting_plan": [c.id for c in plan],
              "posting_plan_reasons": {c.id: why_first(c) for c in plan},
              "ranked": [dict(rank=i + 1, **c.to_dict()) for i, c in enumerate(ranked)],
              "rejected_or_duplicate": [c.to_dict() for c in all_cands if c not in ranked]}
    _dump(out_dir / "promos.json", result)
    _dump(out_dir / "key_moments.json", all_moments)
    from .report import write_csv, write_markdown
    write_csv(ranked, out_dir / "promos_ranked.csv")
    write_markdown(result, ranked, plan, out_dir / "report.md")
    log(f"Done in {meta['seconds']}s: {len(ranked)} ranked promos -> {out_dir}")
    return result


def _dump(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def why_first(c: Candidate) -> str:
    """Plain-English reason a clip is in the posting plan, built from its scores (deterministic)."""
    s = c.scores or {}
    h, cl, st, sp = (_num(s.get(k), 3) for k in ("hook", "cliffhanger", "standalone", "spoiler"))
    parts = []
    visual_only = c.first_line_at is None or c.first_line_at > HOOK_WINDOW
    if visual_only:
        parts.append(f"visual-only opening (first line at {c.first_line_at}s) - check the footage")
    else:
        parts.append("instant scroll-stopper in the first 3s" if h >= 5 else "strong opening" if h >= 4 else "softer opening")
    parts.append("cuts on an unresolved peak" if cl >= 5 else "ends on open tension" if cl >= 4 else "weaker ending")
    if st >= 5:
        parts.append("needs no backstory")
    if sp >= 3:
        parts.append("reveals some mid-season plot")
    first = min(c.episodes)
    if c.kind == "montage":
        parts.append(f"sells the arc across episodes {', '.join(map(str, c.episodes))}")
    elif first <= 5:
        parts.append(f"from Ep {first}, so a new viewer who installs starts right where the clip is")
    else:
        parts.append(f"tests a different story beat (Ep {first})")
    return "; ".join(parts)


def _render_window(ep: Episode, start: float, end: float, pad: float = 25.0) -> str:
    from .parser import batch_id
    lines = []
    for e in ep.events:
        if start - pad <= e.start <= end + pad:
            t = f"[{batch_id(ep.number, e.local_id)} {fmt_time(e.start)}-{fmt_time(e.end)}]"
            if e.kind == "DIALOGUE":
                lines.append(f'{t} {e.speaker}: "{e.text}"')
            elif e.kind == "CAMERA":
                lines.append(f"{t} CAMERA: {e.text[:90]}")
            else:
                lines.append(f"{t} {e.kind}: {e.text}")
    return "\n".join(lines)


def repair_lengths(llm: LLM, cands: list[Candidate], eps: dict[int, Episode], log=print) -> tuple[dict, int]:
    """Code measures, AI fixes: send every single-episode promo that failed ONLY on length back to
    the model with its exact measured length and the surrounding lines (one call for all)."""
    broken = [c for c in cands if c.kind == "single" and not c.valid and c.segments
              and all("duration" in i for i in c.issues if "unknown" not in i)
              and any("duration" in i for i in c.issues)]
    if not broken:
        return {}, 0
    items = []
    for c in broken:
        ep = eps[c.segments[0].episode]
        segs = ", ".join(f"{s.start_event}->{s.end_event} ({s.duration:.1f}s)" for s in c.segments)
        items.append(f"### id={c.id} episode {ep.number} | measured length {c.duration:.1f}s (must be 20-60s)\n"
                     f"current segments: {segs}\nintended hook: {c.hook}\nintended cliffhanger: {c.cliffhanger}\n"
                     f"lines:\n{_render_window(ep, min(s.start for s in c.segments), max(s.end for s in c.segments))}")
    rb = int(os.getenv("REPAIR_BATCH", "50"))
    raw, fixes = [], {}
    for i in range(0, len(items), rb):
        r = llm.json(prompts.REPAIR_SYSTEM, prompts.REPAIR_USER.format(items="\n\n".join(items[i:i + rb])),
                     temperature=0.2)
        raw.append(r)
        fixes.update({str(f.get("id")): f for f in _as_obj(r, "fixes").get("fixes", []) if isinstance(f, dict)})
    repaired = 0
    for c in broken:
        f = fixes.get(c.id)
        if not f:
            continue
        ep = eps[c.segments[0].episode]
        promo = {"title": c.title, "why": c.why, "standalone_gap": c.standalone_gap, "tropes": c.tropes,
                 "self_score": c.self_score, "hook": f.get("hook") or c.hook,
                 "cliffhanger": f.get("cliffhanger") or c.cliffhanger, "segments": f.get("segments"),
                 "hook_event": f.get("hook_event"), "cliffhanger_event": f.get("cliffhanger_event")}
        new, _ = candidates_from_episode({"promos": [promo]}, ep, eps)
        if not new:
            continue
        n = new[0]
        n.id = c.id
        n.fixes.insert(0, f"repaired by LLM after code measured {c.duration:.1f}s")
        if n.valid:
            repaired += 1
            cands[cands.index(c)] = n
        else:
            c.issues.append(f"repair attempt still invalid ({n.duration:.1f}s)")
    log(f"  repaired {repaired}/{len(broken)} length-broken promos")
    return raw, repaired


def trim_long(c: Candidate, eps: dict[int, Episode], head: float = 20.0, tail: float = 22.0) -> Candidate | None:
    """Deterministic fix for too-long single-episode promos: keep the hook opening (~head s) and the
    cliffhanger ending (~tail s), cut the middle. Cuts snap to line boundaries."""
    if c.kind != "single" or not c.segments or c.duration <= MAX_LEN + TOLERANCE:
        return None
    first, last = c.segments[0], c.segments[-1]
    ep = eps[first.episode]
    speech = [e for e in ep.events if e.kind in ("DIALOGUE", "ACTION")]
    # head: end on the last line that finishes within ~head seconds of the start
    head_end = [e for e in speech if first.start + 8 <= e.end <= first.start + head + 2 and e.end <= first.end]
    # tail: start on the first spoken line within the last ~tail seconds
    tail_start = [e for e in speech if e.kind == "DIALOGUE" and last.end - tail - 2 <= e.start <= last.end - 8
                  and e.start >= last.start]
    if not head_end or not tail_start:
        return None
    he, ts = max(head_end, key=lambda e: e.end), min(tail_start, key=lambda e: e.start)
    if ts.start <= he.end:
        return None
    n = Candidate(**{**c.__dict__, "segments": [
        Segment(ep.number, first.start, he.end, first.start_event, he.local_id),
        Segment(ep.number, ts.start, last.end, ts.local_id, last.end_event)],
        "issues": [], "fixes": list(c.fixes), "valid": True})
    n.fixes.insert(0, f"code cut the middle out of a {c.duration:.1f}s clip (kept hook opening + cliffhanger ending)")
    return validate(n, eps)
