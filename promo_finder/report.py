"""Human-readable outputs: CSV for editors, Markdown report for review."""
from __future__ import annotations

import csv
from pathlib import Path


def _segs(c) -> str:
    return " + ".join(f"Ep{s.episode} {s.to_dict()['start']}-{s.to_dict()['end']}" for s in c.segments)


def write_csv(ranked, path: Path) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["rank", "id", "type", "score", "duration_sec", "segments", "title", "hook", "cliffhanger",
                    "why", "hook_score", "cliff_score", "standalone_score", "appeal_score", "spoiler_score",
                    "judge_verdict", "standalone_gap", "auto_fixes", "issues"])
        for i, c in enumerate(ranked, 1):
            s = c.scores or {}
            w.writerow([i, c.id, c.kind, c.final_score, c.duration, _segs(c), c.title, c.hook, c.cliffhanger,
                        c.why, s.get("hook"), s.get("cliffhanger"), s.get("standalone"), s.get("appeal"),
                        s.get("spoiler"), s.get("verdict"), c.standalone_gap, " | ".join(c.fixes),
                        " | ".join(c.issues)])


def write_markdown(result: dict, ranked, plan, path: Path) -> None:
    m = result["meta"]
    used = m.get("models_used") or {}
    used_txt = ", ".join(f"{k} x{v}" for k, v in sorted(used.items(), key=lambda kv: -kv[1])) or "cache only"
    L = [f"# Promo report — {m['title']}", "",
         f"Primary model: `{m['model']}` · calls actually answered by: {used_txt} · episodes: {m['episodes_processed']} · candidates: {m['candidates_total']} "
         f"(valid {m['candidates_valid']}, ranked after dedupe {m['ranked']}) · LLM calls: {m['llm_calls']} "
         f"(cache hits {m['cache_hits']}) · {m['seconds']}s", "",
         "## Post these first", "",
         "| # | id | score | length | segments | hook | cliffhanger | why post it first |",
         "|---|---|---|---|---|---|---|---|"]
    reasons = result.get("posting_plan_reasons", {})
    for i, c in enumerate(plan, 1):
        L.append(f"| {i} | {c.id} | {c.final_score} | {c.duration:.0f}s | {_segs(c)} | {c.hook} | {c.cliffhanger} "
                 f"| {reasons.get(c.id, '')} |")
    L += ["", "Ranking = weighted judge scores (hook 30%, cliffhanger 25%, standalone 20%, appeal 15%, "
          "finder self-score 10%) minus penalties for spoilers, lengths outside 25-50s and context gaps. "
          "The plan caps 2 clips per episode so the first batch tests different story beats, and holds back clips "
          "that spoil a major twist (judge spoiler >= 4). Clips with no spoken line in the first 3s are penalised.", "",
          "## All ranked promos", ""]
    for i, c in enumerate(ranked, 1):
        s = c.scores or {}
        L += [f"### {i}. {c.title}  `{c.id}` — score {c.final_score}",
              f"- **Segments ({c.duration:.1f}s):** {_segs(c)}",
              f"- **Hook:** {c.hook}",
              f"- **Cliffhanger:** {c.cliffhanger}",
              f"- **Why it works:** {c.why}",
              f"- **Judge:** hook {s.get('hook')}/5 · cliff {s.get('cliffhanger')}/5 · standalone "
              f"{s.get('standalone')}/5 · appeal {s.get('appeal')}/5 · spoiler {s.get('spoiler')}/5 — {s.get('verdict')}"]
        if c.standalone_gap:
            L.append(f"- **Context gap:** {c.standalone_gap}")
        if c.fixes:
            L.append(f"- **Auto-fixes:** {'; '.join(c.fixes)}")
        L += ["", "<details><summary>Clip transcript</summary>", "", "```", c.clip_text, "```", "</details>", ""]
    rej = result["rejected_or_duplicate"]
    L += ["## Rejected / duplicates", ""]
    for r in rej:
        L.append(f"- `{r['id']}` ({r['duration_sec']:.0f}s): {'; '.join(r['issues']) or 'not ranked'}")
    path.write_text("\n".join(L), encoding="utf-8")
