"""Deterministic screenplay parser (no AI).

Turns a Kuku timestamped screenplay .txt into structured events so that
every timestamp in our output comes from the script, never from the model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

TIME_RE = r"(\d{1,2}):(\d{2}(?:\.\d+)?)"
TAG_RE = re.compile(r"^\*\*(?P<tag>.+?)\*\*\s*$")
RANGE_RE = re.compile(rf"^Start:\s*\({TIME_RE}\)\s*\|\s*End:\s*\({TIME_RE}\)")
SCENE_RE = re.compile(r"^SCENE\s+(\d+)")
HEADING_RE = re.compile(r"^Scene heading:\s*(.+)")
EP_NUM_RE = re.compile(r"^EPISODE NUMBER:\s*.*?(\d+)")
LOGLINE_RE = re.compile(r"^EPISODE LOGLINE:\s*(.+)")
CONT_RE = re.compile(r"^CONTINUITY NOTES:\s*(.+)")

NON_DIALOGUE = {"CAMERA", "ACTION", "SOUND FX", "SFX", "MUSIC", "VO", "TEXT", "SUPER"}


def to_seconds(m: str, s: str) -> float:
    return int(m) * 60 + float(s)


def fmt_time(t: float) -> str:
    m, s = divmod(max(t, 0.0), 60)
    return f"{int(m):02d}:{s:05.2f}"


@dataclass
class Event:
    id: str            # global id, e.g. "e07_031"
    local_id: str      # id shown to the model within one episode, e.g. "E031"
    episode: int
    scene: int
    location: str
    kind: str          # CAMERA | ACTION | SOUND FX | DIALOGUE
    speaker: str | None
    emotion: str | None
    start: float
    end: float
    text: str


@dataclass
class Episode:
    number: int
    logline: str
    continuity: str
    duration: float
    events: list[Event] = field(default_factory=list)
    source: str = ""

    def by_local(self) -> dict[str, Event]:
        return {e.local_id: e for e in self.events}

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _split_tag(tag: str) -> tuple[str, str | None, str | None]:
    """'LADY JULIANA (Confused, Breathless):' -> ('DIALOGUE', 'LADY JULIANA', 'Confused, Breathless')"""
    tag = tag.strip().rstrip(":").strip()
    m = re.match(r"^(?P<name>[^(]+?)\s*(?:\((?P<emo>.*)\))?$", tag)
    name = (m.group("name") if m else tag).strip()
    emo = m.group("emo").strip() if m and m.group("emo") else None
    if name.upper() in NON_DIALOGUE:
        return name.upper(), None, emo
    return "DIALOGUE", name, emo


def parse_text(raw: str, fallback_number: int = 0, source: str = "") -> Episode:
    lines = [l.rstrip() for l in raw.splitlines()]
    number, logline, cont = fallback_number, "", ""
    scene, location = 0, ""
    events: list[Event] = []
    pending: dict | None = None

    def flush():
        nonlocal pending
        if pending and pending.get("start") is not None:
            txt = " ".join(pending["text"]).strip().strip('"').strip()
            idx = len(events) + 1
            events.append(Event(
                id=f"e{number:02d}_{idx:03d}", local_id=f"E{idx:03d}", episode=number,
                scene=scene, location=location, kind=pending["kind"], speaker=pending["speaker"],
                emotion=pending["emotion"], start=pending["start"], end=pending["end"], text=txt,
            ))
        pending = None

    for line in lines:
        s = line.strip()
        if m := EP_NUM_RE.match(s):
            number = int(m.group(1)); continue
        if m := LOGLINE_RE.match(s):
            logline = m.group(1).strip(); continue
        if m := CONT_RE.match(s):
            cont = m.group(1).strip(); continue
        if m := SCENE_RE.match(s):
            flush(); scene = int(m.group(1)); continue
        if m := HEADING_RE.match(s):
            location = m.group(1).strip(); continue
        if m := TAG_RE.match(s):
            flush()
            kind, speaker, emo = _split_tag(m.group("tag"))
            pending = {"kind": kind, "speaker": speaker, "emotion": emo, "start": None, "end": None, "text": []}
            continue
        if pending is not None:
            if m := RANGE_RE.match(s):
                pending["start"] = to_seconds(m.group(1), m.group(2))
                pending["end"] = to_seconds(m.group(3), m.group(4))
            elif s and not s.startswith("---") and not s.startswith("Scene "):
                pending["text"].append(s)
    flush()

    # ids depend on episode number, which is known by now
    for e in events:
        e.episode = number
        e.id = f"e{number:02d}_{e.local_id[1:]}"
    duration = max((e.end for e in events), default=0.0)
    return Episode(number=number, logline=logline, continuity=cont, duration=duration,
                   events=events, source=source)


def parse_file(path: str | Path) -> Episode:
    path = Path(path)
    m = re.search(r"episode-(\d+)", path.name, re.I)
    return parse_text(path.read_text(encoding="utf-8", errors="replace"),
                      fallback_number=int(m.group(1)) if m else 0, source=path.name)


def parse_folder(folder: str | Path) -> list[Episode]:
    eps = [parse_file(p) for p in sorted(Path(folder).glob("*.txt"))]
    return sorted(eps, key=lambda e: e.number)


def batch_id(episode: int, local_id: str) -> str:
    """Id shown to the model when several episodes share one prompt: '07-E031'."""
    return f"{episode:02d}-{local_id}"


def render_for_llm(ep: Episode, camera_chars: int = 140) -> str:
    """Compact numbered transcript. CAMERA lines are kept (visual hooks like slaps
    live there) but truncated to save tokens. Ids are episode-prefixed (07-E031)
    so several episodes can share one prompt."""
    out = []
    cur_scene = None
    for e in ep.events:
        if e.scene != cur_scene:
            cur_scene = e.scene
            out.append(f"\n-- SCENE {e.scene}: {e.location} --")
        t = f"[{batch_id(ep.number, e.local_id)} {fmt_time(e.start)}-{fmt_time(e.end)}]"
        if e.kind == "DIALOGUE":
            emo = f" ({e.emotion})" if e.emotion else ""
            out.append(f'{t} {e.speaker}{emo}: "{e.text}"')
        elif e.kind == "CAMERA":
            txt = e.text if len(e.text) <= camera_chars else e.text[:camera_chars] + "…"
            out.append(f"{t} CAMERA: {txt}")
        else:
            out.append(f"{t} {e.kind}: {e.text}")
    return "\n".join(out).strip()
