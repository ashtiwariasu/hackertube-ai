# Community packs: data contract

A pack is everything one community (or the open web) contributes to the catalogue, in its own folder
`packs/<pack>/`. A pack never touches the app; `scripts/build_catalog.py` merges every pack into the catalogue.

## Inclusion rule (same for every community, published on the site)
A video qualifies when, within that community:
- at least 3 different authors linked it,
- in at least 3 different calendar months,
- each counted mention has at least 100 characters of the author's own text (links stripped).
Keep at most 20 mentions per video, favouring different authors, then recommendations.

## Files in `packs/<pack>/`

### `videos.jsonl` — one qualifying video per line
```json
{"vid": "11-char YouTube id", "community": "hn" | "se:<site>" | "bluesky",
 "title": "...", "channel": "...",            // from YouTube Data API videos.list
 "summary": "2-3 sentences: what the video is",
 "why": "one sentence: why this community points people to it",
 "content_type": "talk|lecture|course|documentary|explainer|interview|podcast|tutorial|demo|news|commentary|comedy|music|other",
 "level": "general|intro|intermediate|advanced",
 "shelf_life": "evergreen|dated|news",
 "primary_topic": "short phrase", "tags": ["..."],
 "categories": ["category id from app/data/taxonomy.json", "... 1 to 3, best first"],
 "quote": {"text": "verbatim excerpt from one mention", "mention_id": "...", "year": "YYYY"} | null,
 "stats": {"mentions": n, "authors": n, "recommenders": n, "rec_months": n, "strong_recs": n,
           "stances": {"rec": n, "strong_rec": n, "reference": n, "critical": n, "joke": n},
           "first_year": YYYY, "last_year": YYYY},
 "score": rec_months + 0.5 * (months with a strong_rec)}
```
Drop videos that YouTube reports deleted, private, or not embeddable.

### `mentions.jsonl` — one labelled mention per line, only for videos in videos.jsonl
```json
{"id": "community-unique id", "url": "link to the post/comment", "vid": "...", "community": "...",
 "author": "username or user id", "month": "YYYY-MM", "context": "story / question title",
 "stance": "strong_rec|rec|reference|critical|joke",
 "why": "<=15 words: what a viewer gets from the video, per this mention ('' for joke/critical)",
 "quote": "verbatim excerpt that occurs in text, may be ''",
 "text": "author text, HTML stripped, links replaced by [VIDEO] (this video) or [LINK], max 1500 chars"}
```
Stance meanings: strong_rec = enthusiastic recommendation; rec = points people to it;
reference = cites it neutrally; critical = criticises or debunks it; joke = meme/punchline.

### `REPORT.md` — counts at each filter stage, models used, tokens and US$ spent, problems, licence notes.

## Web pack: `packs/web/videos.jsonl` (the open web, from Common Crawl)

The open-web source counts websites, not people: for each video, how many independent sites linked it and
across how many monthly crawls (see `pipeline/README.md`). It is not a community pack:
the rows have no `community`, no card fields and no `mentions.jsonl`. The build reads exactly one web pack,
`packs/web/` by default or the folder given with `--web-pack DIR` (`packs/web-sample/` is a 20-row example
for local runs); folders named `web` or `web-*` are skipped by the community-pack loop.

One row per video:
```json
{"vid": "11-char YouTube id",
 "domains": 12,                      // independent linking sites (after the source filters of phase A2)
 "months": 4,                        // distinct monthly crawls that contained a link
 "first_seen": "2026-01", "last_seen": "2026-10",   // YYYY-MM of the earliest and latest crawl
 "top_domains": ["example.org", "..."],             // up to 5 hostnames, most links first
 "why": "one sentence: why the web points people to it"}   // optional
```
Rules: `domains` and `months` are integers of at least 1; `first_seen` is not after `last_seen`; a repeated `vid`
keeps the row with the higher (domains, months). Videos that YouTube reports deleted, private or not embeddable
are dropped at build time like every other source.

What the build does with it (`scripts/build_catalog.py`, helpers in `app/web_source.py`):
- every listed video gets `v["web"] = {domains, months, first_seen, last_seen, top_domains}` (plus `why` when given),
  and the community entry `web` ("The open web") appears in `communities.json` with the count;
- a video with an HN or SE source gets a ranking bonus of `0.5 * min(log2(1 + domains), 3)`, like the Wikipedia bonus;
- a video only the web pack knows gets a base record from YouTube metadata (title, channel, duration, views; no summary,
  no categories), an empty `comm`, `recs` 0, an empty badge, and `score = bonus + 0.5 * min(months, 6)`. It is searchable
  and in the personal feed but stays out of the home rows and the ranked lists unless `HT_WEB_ONLY_ROWS=1` is set
  when the app starts, or the visitor filters the home feed by "The open web".

## Models and money
- Anthropic key: `gcloud secrets versions access latest --secret ANTHROPIC_API_KEY --project $PROJECT`
  (pass it as an env var to commands; never write it into files or logs).
- Stance labelling: `claude-haiku-5-5` via the Message Batches API (50% price). Cards: `claude-sonnet-5-5` via Batches.
  Use structured outputs (`output_config.format` JSON schema) so results parse. Read the claude-api skill docs if unsure.
- YouTube metadata: key from `gcloud secrets versions access latest --secret hackertube-youtube-api-key --project $PROJECT`,
  `videos.list` with up to 50 ids per call (1 quota unit each).

## Running on a GPU box
The labelling steps run on any machine with a big GPU (we used one H100 PCIe with vLLM). Work under
`~/packs/<pack>/` on the box, run long jobs with `nohup` and poll them, and keep SSH sessions short.
