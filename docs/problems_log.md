# Problems log (raw material for the solution doc)

| # | Problem | Cause | What we did |
|---|---|---|---|
| 1 | Every call failed with 404 | `gemini-2.5-flash` isn't available to new API keys | Listed the models the key can use; switched the default to `gemini-3.6-flash` |
| 2 | Run hung for 10+ minutes with no output | `gemini-3.8-flash` was overloaded (503) and the Google SDK kept retrying silently | 120s timeout per call, SDK retries turned off, our own fallback chain (3.6 → 3.5 → 3.8 → 3.5-lite); 400/401/404 now fail fast |
| 3 | Retry loop retried a 404 five times | Retry logic treated every error as temporary | Errors are now split into retryable (503/429/timeout → switch model) and permanent (bad key/model → stop) |
| 4 | One reply wasn't valid JSON ("Extra data") | LLM output formatting | Automatic retry; it succeeded on the second attempt |
| 5 | Full run crashed at Ep 4 | Model returned `[{"promos": ...}]` instead of `{"promos": ...}` | Normaliser accepts both shapes; one bad episode is now logged and skipped instead of stopping the run |
| 6 | **AI got it wrong:** the top pick's hook was described as "knocks out a guard" | The punch lands at about 14s; the first 3s are only the line "Take your hands off her" | Found by checking against the script by hand. The cut points were right but the description was oversold, so hook descriptions still need a human check |
| 7 | Fix log showed no-op fixes ("01:28→01:28") | Off-by-epsilon condition in the cut-snapping code | Fixed the condition |
| 8 | Full run fell back to the weakest model for 42 of 51 calls | Free tier allows only **20 requests/day per Flash model**; I had assumed a per-minute limit | Batching (5 episodes per call): a full run went from 51 calls to 14 and from 33 min to 91s. Models that hit their daily limit are now dropped from the chain instead of retried |
| 9 | Report header said `gemini-3.6-flash` while most answers came from flash-lite | Header showed the configured model, not the one that answered | Header now lists the calls each model actually answered |
| 10 | Flash-lite chose clips of 70–112s and 6–14s | Weak model can't add up times | Code cuts the middle out of long clips (kept 21). The AI repair for short clips fixed only 1 in 9: **AI got it wrong again**, and once made a clip longer (75→91s) |
| 11 | Montages were all too short (10–17s) | Key moments are 3–15s and the model chose only 2 | Prompt asks for 3–4 moments and a written sum; 2 montages now pass |
| 12 | Duke-reveal spoilers (Ep42, Ep45) were in the "post first" list | Spoiler penalty too weak | Clips rated spoiler ≥4 are kept out of the first batch |
| 13 | Judge gave a 5/5 hook to openings with no line for 7–10s | Judge reads text and can't see footage | Code measures when the first spoken line lands and penalises/flags anything after 3s |
| 14 | Batched run returns fewer promos per episode (~1 vs ~1.5) | Model does less work per episode in a long prompt | Trade-off accepted for the free tier; with a paid key, use `--batch-size 1` |
| 15 | User's terminal said `run.py` not found | Command was run from `kuku_assignment` instead of `promo-finder` | Added the `cd promo-finder` step to the instructions |
| 16 | A new Gemini key still hit the daily limit | The free quota is per Google Cloud *project*, not per key | Switched provider to Groq (only a `.env` change, which shows the model swap works) |
| 17 | Groq: `llama-3.3-70b-versatile` returned 404 | Model retired; I had suggested it from memory | Listed the models on the key; using `openai/gpt-oss-120b` |
| 18 | Groq allows only 8,000 tokens/minute, but our 5-episode batches are ~11k | Opposite limit shape to Gemini (Groq: 1,000 requests/day, small per-minute budget) | All prompt sizes are now `.env` settings (1 episode per call, judge 6 per call, montage capped at 40 moments, repair 3 per call). The code waits as long as Groq says when rate-limited |
| 19 | `.env` changes were lost once | VS Code had the old file open and saved over the new one | Rewrote it; close `.env` in the editor before letting tools change it |
| 20 | Code would have sent the Groq key to Google after switching back | Key lookup wasn't provider-specific | Each provider now reads only its own key |
| 21 | Judge scores differ between models (gpt-oss gives hooks 2–3/5, flash-lite 4–5/5) | Each model calibrates differently | Compare the *order* within one run, never absolute scores across models |
| 22 | Groq run stopped at Ep 41 of 50 | Groq also has a **daily token budget** per model; ~40 episodes × ~6k tokens used it up | Added fallback Groq models (`qwen3.8-27b`, `gpt-oss-20b`), each with its own budget; resumed from cache so the 40 finished episodes weren't re-billed |
| 23 | After that, every remaining call crashed with "integer modulo by zero" | Bug: the code didn't handle an empty model list | Now stops with a clear message saying finished work is cached |
| 24 | Episodes 41–44 and 46 were lost in the resumed run | Backup model `qwen3.8-27b` kept failing Groq's JSON check (`json_validate_failed`), and the code retried the same model 8 times | After 2 invalid-JSON replies the tool now moves to the next model; `gpt-oss-20b` (reliable JSON) goes first in the fallback list; resumed only the missing episodes |
