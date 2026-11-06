"""CLI.

  python run.py <show_folder>                      # all episodes
  python run.py <show_folder> --episodes 7         # just episode 7
  python run.py <show_folder> --episodes 5-10,12   # a range / list
  python run.py <show_folder> --max-episodes 3     # first 3 (quick test)
"""
import argparse
import re
from pathlib import Path

from promo_finder.llm import LLM
from promo_finder.parser import parse_folder
from promo_finder.pipeline import parse_episode_spec, run

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Find and rank promo clips in a microdrama screenplay pack")
    ap.add_argument("input", help="folder containing episode .txt screenplays")
    ap.add_argument("--title", help="show title (default: folder name)")
    ap.add_argument("--out", help="output folder (default: outputs/<show-slug>[-ep<spec>])")
    ap.add_argument("--episodes", help="which episodes to search, e.g. 7 or 5-10,12 (default: all)")
    ap.add_argument("--max-episodes", type=int, help="only process the first N episodes (for quick tests)")
    ap.add_argument("--batch-size", type=int, help="episodes per LLM call (default 5; 1 = one call per episode)")
    ap.add_argument("--provider", help="override LLM_PROVIDER")
    ap.add_argument("--model", help="override LLM_MODEL")
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()

    folder = Path(a.input)
    title = a.title or folder.name
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]
    only = parse_episode_spec(a.episodes) if a.episodes else None
    suffix = f"-ep{a.episodes.replace(',', '_')}" if a.episodes else ""
    out = Path(a.out or f"outputs/{slug}{suffix}")
    episodes = parse_folder(folder)  # whole show is parsed so the model gets story-so-far context
    print(f"Parsed {len(episodes)} episodes from {folder}" + (f"; searching episodes {sorted(only)}" if only else ""))
    run(episodes, title, out, llm=LLM(provider=a.provider, model=a.model, use_cache=not a.no_cache),
        max_episodes=a.max_episodes, only=only, batch_size=a.batch_size)
