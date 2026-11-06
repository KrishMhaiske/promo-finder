# Promo Finder — Kuku AI Generalist Assignment (Problem 1)

Reads a microdrama's timestamped screenplays and returns ranked 20–60s promo clips. Each clip gets exact segment timestamps, a hook, a cliffhanger and a one-line reason it works.

## How it works

```
screenplays ─► parser (code) ─► finder, 5 episodes per call (LLM) ─► validator + auto-fix (code)
                                         │                                   │ too long → code cuts middle
                                         │ key moments                       │ too short → LLM repair (1 call)
                                         ▼                                   ▼
                           cross-episode montages (LLM) ─► judge (LLM) ─► rank + dedupe + posting plan (code)
```

- **No invented timestamps.** The model only picks line IDs (`E031`). All times come from the script.
- **Rules are checked in code:** total length 20–60s, hook within the first 3s (otherwise the start is trimmed to the hook), cuts never chop a spoken line, segments in order, and overlapping clips are deduplicated. Anything rejected is listed with its reason.
- **Code measures, AI decides:** too-long clips are cut by code (keep hook + cliffhanger, drop the middle); too-short ones go back to the LLM once with their exact measured length. Code flags clips whose first spoken line lands after 3s (the judge can't see footage), and the posting plan holds back clips rated as big spoilers.
- **Ranking:** a strict LLM judge scores hook / cliffhanger / standalone / appeal / spoiler from 1 to 5. The weighted sum is computed in code, with penalties for spoilers and lengths outside 25–50s. The posting plan allows at most 2 clips per episode.
- **Model-agnostic:** set `LLM_PROVIDER` / `LLM_MODEL` to use Gemini or any OpenAI-compatible API (OpenAI, Groq, OpenRouter…). A fallback chain handles 503 and rate-limit errors. Responses are cached on disk, so re-runs are free.

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env          # add your free Gemini key from https://aistudio.google.com/apikey
python run.py "path/to/show_folder"                 # all episodes
python run.py "path/to/show_folder" --episodes 7          # one episode
python run.py "path/to/show_folder" --episodes 5-10,12    # range / list
python run.py "path/to/show_folder" --batch-size 1        # one call per episode (more candidates, more calls)
streamlit run app.py          # UI: upload .txt files / .zip, or browse saved results
```

Outputs go to `outputs/<show>/`: `report.md` (readable), `promos_ranked.csv` (for editors), `promos.json` (everything, including rejected candidates) and `raw_llm/` (unedited model responses).

## Swap the model

```bash
LLM_PROVIDER=openai_compatible LLM_BASE_URL=https://api.openai.com/v1 LLM_MODEL=gpt-4o LLM_API_KEY=... python run.py show/
python run.py show/ --model gemini-3.5-flash
```

The input screenplays are confidential and are **not** included in this repo (`*.txt` is git-ignored).

## Cost and scale
Two tested provider profiles (only `.env` differs):
- **Gemini profile:** `BATCH_EPISODES=5` → about 14 calls, about 90 s per 50-episode show (fits 20 requests/day).
- **Groq profile** (`openai/gpt-oss-120b`, 8k tokens/minute): `BATCH_EPISODES=1`, `JUDGE_BATCH=6`, `MONTAGE_MAX_MOMENTS=40`, `REPAIR_BATCH=3`, `LLM_MIN_INTERVAL=40`, `LLM_EXTRA_BODY={"reasoning_effort": "low"}`, `LLM_FALLBACK_MODELS=openai/gpt-oss-20b` → about 60 calls, about 45 min.

A 50-episode show (about 108k input tokens) takes **about 15 LLM calls and about 90 seconds**, which fits the Gemini free tier (20 requests/day per model). On a paid Flash-class model that's roughly a few cents per show. Responses are cached, so re-ranking after a rules change costs 0 calls.

## Docs
- `docs/quality_review.md`: hand check of the top 10 and where the tool picks badly
- `docs/problems_log.md`: every problem hit while building, and what was done about it

## Deploy (Streamlit Community Cloud)
Point the app at `app.py` and add `GEMINI_API_KEY` under *Secrets*. Users can upload a show (.txt files or a .zip) to run it, or drop in a `promos.json` to browse a saved run.
