# Quality review

## Final run: run 3 (Groq `gpt-oss-120b` for Ep 1–41, `gpt-oss-20b` for Ep 42–50 and the judge)
Hand check of the 10 clips in the posting plan, against the screenplay lines each one cuts.

| Plan # | Clip | Hook ≤3s | Cliffhanger | Standalone | Verdict |
|---|---|---|---|---|---|
| 1 | Ep12 00:57.30–01:27.21 (30s): bare-hands-in-the-thorns ultimatum | ✅ line at 0.0s | ✅ "Tell me where the garden is." | ✅ | **Great**: tighter than run 2's version of the same scene |
| 2 | Ep18 00:20.10–01:13.50 (53s): fire rescue | ✅ "Juliana! Get up!" at 0.7s | ✅ she runs *into* the fire for the ledger | ✅ | **Great**, but the AI's cliffhanger text ("beam crashes") doesn't match the script ending |
| 3 | Ep16 00:14.30–00:52.20 (38s): catches the club, beats 3 thugs | ✅ action at 0.1s | ✅ "Gerald Harrington paid us!" | ✅ | **Great** |
| 4 | Ep24 00:32.50–01:03.80 (31s): riding-crop strike | ✅ strike at 0.0s | ⚠️ a threat, not a question | ✅ | Good (a classic slap-style opener) |
| 5 | Ep3 01:09.90–01:28.00 (18s): "Take your hands off her" | ✅ | ✅ the groom panics after the punch | ✅ | Good but short (accepted in the ±3s tolerance) |
| 6 | Ep27 (20s, 2 segments): "This ring..." → doors burst open | ✅ | ✅ | ⚠️ which ring? | Good |
| 7 | Ep30 (58s): villains plotting | ✅ | ⚠️ | ❌ no protagonist on screen, long | **Weak** |
| 8 | Ep11 (26s, 3 segments): search → watch found | ⚠️ "A complaint is not proof" is softer than run 2's "stole my watch!" | ✅ | ✅ | Usable; run 2's cut was better |
| 9 | Ep26 (29s): the ring is noticed | ❌ first line at 9.5s (visual only; flagged by code) | ✅ | ✅ | **Weak** opening |
| 10 | Ep35 (45s, code-trimmed from 113s): mother finds him alive | ✅ | ✅ "the same men ruling Ashwood now" | ⚠️ | Usable |

**Score: 6 good, 2 usable, 2 weak.** Run 2 scored 4 / 3 / 3.
The spoiler filter worked: ranks 4–6 (Ep39, Ep41, Ep50, the Duke reveals) were held out of the plan automatically.

### Run 2 vs run 3 (same pipeline, only `.env` changed)
| | Run 2: Gemini flash-lite, 5 eps/call | Run 3: Groq gpt-oss, 1 ep/call |
|---|---|---|
| Promos ranked / episodes covered | 45 / 42 of 50 | **55 / 48 of 50** |
| Clips needing code trimming | 21 | **6** |
| Openings with no spoken line in first 3s | 8 | **5** |
| Posting plan, episodes used | mostly late (11–41) | **spread out (3–35)** |
| Hand check of plan (good / usable / weak) | 4 / 3 / 3 | **6 / 2 / 2** |
| Calls / time | 14 / 91s | ~60 / ~45 min (Groq allows only 8k tokens per minute) |

Caveats: run 3 mixes models (Groq's daily token budget ran out), and judge scores aren't comparable across models (gpt-oss scores hooks ~3/5, flash-lite ~4/5). Compare the *order* within a run, not the absolute scores.

## Earlier run: hand check of the top 10 (run 2, Gemini flash-lite)

**How it was checked:** each of the top 10 picks in run 2 (`outputs/run2_batched_flash-lite`, model `gemini-3.5-flash-lite`) was read against the screenplay lines it cuts. Every line in a clip comes straight from the script, so the timestamps can be trusted. What's being judged here is the *editorial* choice.

Questions asked for each clip: (1) is there a hook in the first 3 seconds? (2) does it cut on an unresolved moment? (3) would a stranger follow it? (4) does it spoil a major twist? (5) are the cuts clean?

| Original rank | Clip | Hook ≤3s | Cliffhanger | Standalone | Spoiler | Cut quality | Verdict |
|---|---|---|---|---|---|---|---|
| 1 | Ep41 "Bring her in" → chapel explodes (43s, code-trimmed) | ✅ line at 0.0s | ✅ explosion | ⚠️ who is Catherine? why chained? | medium | ✅ | Good, but needs more context than an early-episode clip |
| 2 | Ep39 Duke walks out in the snow (30s, LLM-repaired) | ❌ first line at 7.3s, visual only | ⚠️ a vow, not a question | ✅ | medium | ✅ | **Overrated.** The judge scored hook 5/5 from a written description of a coat |
| 3 | Ep11 "Your husband stole my gold pocket watch!" (41s, code-trimmed) | ✅ 0.0s | ✅ watch found in his coat | ✅ | low | ✅ jump reads naturally | **Best pick** |
| 4 | Ep12 "I want to see you crawl, sister" (43s) | ✅ 0.0s | ✅ "Tell me where the garden is" | ✅ | low | ✅ one continuous take | **Best pick** |
| 5 | Ep23 "Poison?" → door creaks open (50s) | ✅ 0.5s | ✅ | ⚠️ who is being poisoned? | low | ❌ two 7-second gaps with no dialogue | OK, needs tightening |
| 6 | Ep28 "Lady Juliana Ashcroft" identity exposed (48s) | ✅ 0.2s | ✅ "the gallows" | ✅ the monologue recaps the backstory | medium | ✅ | Good |
| 7 | Ep45 "You're the Duke?" + marriage voided (40s, code-trimmed) | ✅ | ✅ | ⚠️ | ❌ **gives away the central twist** | ❌ jumps from reunion to priest | Should not lead the campaign |
| 8 | Ep42 Duke revealed in the cathedral (44s, code-trimmed) | ❌ first line at 10.2s | ✅ | ✅ | ❌ **central twist** | ✅ | Should not lead the campaign |
| 9 | Ep3 "Guards! Remove her!" → proposal (23s, 3 segments) | ✅ | ✅ | ✅ | low | ❌ 4s + 6s fragments; the punch got cut | Run 1's version of this clip (48s) was much better |
| 10 | Ep14 bloodied hands → clenched fist (42s, code-trimmed) | ✅ | ⚠️ a mood, not a question | ⚠️ "you have taken my place today" is unclear | low | ✅ | OK |

**Score: 4 clearly good, 3 usable with edits, 3 shouldn't be posted first.**

## Where the tool picks badly (systematic)
1. **It can't see the footage.** The judge reads text, so "Alexander emerges in full ducal splendor" earns a 5/5 hook even though nobody speaks for 7 seconds. *Fix applied:* code measures when the first spoken line lands, flags clips where it's after 3s and penalises them. A human still has to check visual hooks in the footage.
2. **Spoilers.** The two Duke-reveal clips (Ep42, Ep45) gave away the show's main payoff but scored high. *Fix applied:* clips the judge rates 4 or higher for spoilers stay in the ranking but are kept out of the "post first" batch.
3. **The weak model can't add up times.** Flash-lite chose clips of 70–112s (21 of 54) and of 6–14s (9 of 54). *Fix applied:* code cuts the middle out of long clips (hook + cliffhanger). The AI repair for short clips worked only 1 time in 9.
4. **Batching costs variety.** With 5 episodes per call, the model returns about 1 promo per episode instead of about 1.5, and early-episode options shrink. The posting plan then leans on late episodes that need backstory.
5. **Code-trimmed jump cuts can lose context** (Ep45), and continuous clips can carry dead air (Ep23). Neither is detected automatically yet.
6. **The judge is generous.** The average hook score is 3.8/5, even though the prompt says most clips are a 3. The ranking order is useful; the absolute scores aren't.

## Human in the loop (recommended workflow)
The tool turns about 100 minutes of episodes into a ranked shortlist of about 45 clips with exact timestamps. An editor still (a) watches the top 10–15 to confirm visual hooks, (b) vetoes spoilers and confusing jump cuts, and (c) picks the first batch. That's about 15 minutes per show, compared with watching every episode.
