# hn-fresh: HN mentions 2026-08-01 to 2026-11-03

Run finished 2026-11-03 02:04 UTC by `refresh_hn.py`; this run re-fetched from 2026-08. Counts cover everything cached since the first run (Algolia hit counts are this run's).

## Counts at each stage

| stage | count |
|---|---|
| old catalogue videos | 6454 |
| old mentions | 39834 |
| algolia hits: query 'youtube', comments | 8660 |
| algolia hits: query 'youtube', storys | 3170 |
| algolia hits: query 'youtu.be', comments | 1385 |
| algolia hits: query 'youtu.be', storys | 71 |
| cached HN items with a YouTube-ish word (all runs) | 13159 |
| (item, video) pairs with a YouTube video link | 6083 |
| distinct items with a video link | 5477 |
| old-data mentions in window rejected by this run's own-text rule (quotes not counted) | 4 |
| pairs with >=100 chars own text | 4144 |
| already in old mentions.jsonl (overlap) | 97 |
| new mentions (recent window) | 4047 |
| videos with new mentions | 3590 |
|   of which already in catalogue | 252 |
|   of which not in catalogue (candidates) | 3338 |
| historical mentions backfilled for candidates (>=100 chars) | 1235 |
| candidates meeting the inclusion rule (3 authors, 3 months) | 102 |
| candidates dropped: deleted/private/not embeddable | 5 |
| new qualifying videos | 97 |
| new mentions needing labels (catalogue + qualifying videos) | 710 |
| estimate before submit, uncached work this run (US$): labels | 0.0 |
| estimate before submit, uncached work this run (US$): cards | 0.0 |
| new mentions labelled | 710 |
| label quotes blanked (not verbatim) | 63 |
| cards written (new videos.jsonl rows) | 97 |
| card quotes not verbatim (replaced or nulled) | 10 |
| mentions.jsonl rows | 710 |
|   for new videos (kept, <=20 each) | 409 |
|   for catalogue videos (all new) | 301 |
| catalogue videos updated (updated_stats.jsonl rows) | 252 |

## Top new videos

- Eliezer Yudkowsky – AI Alignment: Why It's Hard, and Where to Start (Machine Intelligence Research Institute), score 5.5, 3 authors
- Reinventing Entropy | Compression is Intelligence Part 1 (3Blue1Brown), score 5.0, 7 authors
- The Langlands Program - Numberphile (Numberphile), score 4.5, 3 authors
- The One-Electron Universe | Space Time (PBS Space Time), score 4.5, 3 authors
- Rupert's Snub Cube and other Math Holes (suckerpinch), score 4.0, 5 authors
- An Enterprise IPv6 Address Planning Case-Study (Rocky Mountain IPv6 Taskforce), score 3.5, 3 authors
- Patrick Stewart as Macbeth ("Tomorrow, and Tomorrow, and Tomorrow") (petrogulak), score 3.5, 4 authors
- An intuitive approach for understanding electricity (AlphaPhoenix), score 3.5, 3 authors
- Plain Text - Dylan Beattie - NDC Oslo 2021 (NDC Conferences), score 3.5, 3 authors
- David Beazley | Keynote: Built in Super Heroes (PyData), score 3.5, 3 authors
- Refueling a NUCLEAR REACTOR - Smarter Every Day 311 (SmarterEveryDay), score 3.5, 3 authors
- The Adventures of Reemo Green (Neural Viz), score 3.0, 3 authors
- Getting Detained by Counter Terrorism (Britannica), score 3.0, 3 authors
- Navier-Stokes Equations - Numberphile (Numberphile), score 3.0, 3 authors
- Large Language Models explained briefly (3Blue1Brown), score 3.0, 3 authors

## Biggest score gains among catalogue videos

- RcVA8Nj6HEo: 1.0 -> 4.0 (+2 mentions)
- Eq3bUFgEcb4: 6.5 -> 9.0 (+7 mentions)
- KtQ9nt2ZeGM: 6.5 -> 9.0 (+3 mentions)
- jvPPXbo87ds: 15.0 -> 17.5 (+2 mentions)
- kCc8FmEb1nY: 10.0 -> 12.5 (+2 mentions)
- 60z_hpEAtD8: 9.0 -> 11.0 (+2 mentions)
- d-7o9xYp7eE: 13.0 -> 15.0 (+3 mentions)
- B2482h_TNwg: 4.0 -> 5.5 (+1 mentions)
- DUGGJpn2_zY: 2.0 -> 3.5 (+1 mentions)
- Iq9DzN6mvYA: 3.0 -> 4.5 (+1 mentions)

## Models, tokens, spend

- Stance labels: `claude-haiku-5-5`, Message Batches, structured output {stance, why, quote}, effort low.
- Cards: `claude-sonnet-5-5`, Message Batches, structured output (card fields + 1-3 taxonomy ids), effort low.

| scope | batch | model | ok | errored | input tok | output tok | US$ |
|---|---|---|---|---|---|---|---|
| earlier run | msgbatch_01Rm9AMmbmq7GTF3wkebtdpP | claude-haiku-5-5 | 710 | 0 | 583649 | 52723 | 0.0424 |
| earlier run | msgbatch_01CqibpvgS2CDMPDbwrCkiPH | claude-sonnet-5-5 | 97 | 0 | 828087 | 32552 | 0.9908 |

**Total spent, all runs: US$1.0332** (1,411,736 input + 85,275 output tokens). This run: US$0.0000 (budget per run US$10.00; a re-run reuses cached labels and cards, so it only pays for new mentions and videos).
Estimate before submitting (2026-11-03 01:54): US$0.097 for 710 labels + US$1.275 for 97 cards.
Prices used: batch rates (50% of list), Haiku 5.5 $0.05/$0.25 and Sonnet 5.5 $1/$5 per M input/output tokens.

## Method notes

- Source: HN Algolia `search_by_date`, queries `youtube` and `youtu.be`, tags `comment` and `story` (story_text only), walked backwards in time windows of <=1,000 hits, at most 4 requests/s.
- Video ids from watch?v=, youtu.be/, /embed/, /shorts/, /live/, /v/ links (including youtube-nocookie).
- Own text: HTML stripped, links removed, and quoted lines (starting with `>`) not counted; >=100 chars.
- The old mentions.jsonl only holds mentions of the 6,454 catalogue videos, so for every other video seen in the new window the script searched HN's full history for its id (Algolia `search`, one request per id) and counted those older comments as mentions too (they were labelled like the new ones).
- Overlap with the old data (it runs into 2026-08) is removed by (comment id, video id).
- Inclusion rule over old + new + backfilled mentions; at most 20 kept per video, one per author first, recommendations first, then new months, then newest. Stats and score use the kept set, exactly as the open dataset does (formulas checked against all 6,454 old rows).
- mentions.jsonl holds every new labelled mention of an old catalogue video, plus the kept (<=20) mentions of each new video. Mentions of videos that still do not qualify are not labelled or written.
- updated_stats.jsonl adds `old_score` and `kept_ids` (the ids in the 20-mention kept set) beyond the requested fields, so the merge can pick which mentions to show.
- Quotes: label quotes are kept only if they occur verbatim in the mention text; card quotes must occur verbatim in the named mention, else the first recommending mention's verified quote is used, else null.

## Problems

- HUEvRyemKSg: score fell 7.0 -> 6.0 because the 20-mention cap now favours a new author over an older recommending mention

## Licence notes

- HN comments: retrieved from the public HN Algolia API; comments remain the work of their authors (Hacker News data by Y Combinator). The base dataset is HackerTube open data, ODC-By 1.0, which credits the open-index/hacker-news archive.
- YouTube titles and channel names come from the YouTube Data API and belong to their creators; descriptions were only sent to the model and are not stored in the outputs (the raw API cache in `work/` is private and must not be redistributed; refresh it periodically per YouTube's policies).
- Stances, whys and cards are LLM-written: expect a few percent of noise.
