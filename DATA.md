# HackerTube AI data

The catalogue behind [hackertube.ai](https://hackertube.ai): YouTube videos that Hacker News, 117 Stack Exchange sites
and the open web keep pointing people to. It extends the [HackerTube](https://hackertube.org) dataset (6,454 videos
from Hacker News) to 47,126 videos. Counts and the export date are in `MANIFEST.json`; licences in `LICENSE-DATA.md`.

Each release comes as two zips with the same content in two shapes. Pick the one you need.

## `hackertube-format/`: drop-in for anything built on HackerTube's data

The same files and fields as HackerTube's own download (its `DATA.md` documents them in full), so code written for
that download works unchanged and simply sees more videos. New fields are added; none are renamed or removed.

| file | one row is | notes |
|---|---|---|
| `videos.jsonl` | a video, best-scored first | HackerTube's fields, plus the ones below |
| `mentions.jsonl` | a labelled Hacker News comment linking a video | HackerTube's schema exactly |
| `mentions_other.jsonl` | a labelled Stack Exchange post or comment | new; schema below |
| `related.jsonl` | a video's co-recommended videos | HackerTube's graph, for Hacker News videos |
| `taxonomy.json` | the topic tree (16 areas, 150 categories) | unchanged |
| `embeddings.npy` + `embedding_ids.json` | a video's 384-d vector | same model (bge-small-en-v1.5); HackerTube's own vectors for its videos |

New fields in `videos.jsonl`:

| field | meaning |
|---|---|
| `why` | one sentence on why its main source points people to it (for open-web videos, a model's summary) |
| `primary_source` | `hn`, `se:<site>` (e.g. `se:stackoverflow`) or `web` |
| `sources` | per community that recommends it: `why`, `recommenders`, `rec_months`, `mentions`, `first_year`, `last_year` |
| `web` | open-web evidence, or null: `domains` (independent linking sites), `months` (monthly crawls with a link), `first_seen`, `last_seen`, `top_domains` (up to 5), `reputable` (linking sites in the Tranco top 100,000), `why`, `kind`, `area` |
| `wikipedia` | Wikipedia articles citing it, or null: `articles`, `titles` |

For videos that aren't from Hacker News, HackerTube's `hn` block is present with zero counts and `why_hn` is empty, so
code that filters on `hn.recommenders` will still only see Hacker News picks. Use `primary_source`, `sources` and
`why` to show the rest. `score` is HackerTube AI's cross-source score (months recommended in each community, plus a
smaller bonus for independent sites linking it), so it isn't comparable with HackerTube's own scores.

`mentions_other.jsonl`: `source`, `id`, `url`, `vid`, `author`, `month` (`YYYY-MM`), `context` (the question or
thread title), `stance` (`strong_rec`, `rec`, `reference`, `critical`, `joke`), `why`, `quote`, `text` (links shown
as `[VIDEO]` or `[LINK]`), `license`.

## `app-data/`: drop-in for this app

Unzip it over `app/data` in a checkout of [the repo](https://github.com/ashtiwariasu/hackertube-ai) and restart.
`videos.json`, `mentions.json`, `related.json`, `authors.json`, `taxonomy.json`, `communities.json`,
`topic_intros.json`, `embeddings.npy` and `title_emb.npy` are the app's own formats (see `app/catalog.py`).

## What isn't included

YouTube's API terms don't allow redistributing its metadata, so there are no descriptions, view counts, durations,
publish dates or channel ids; titles and channel names are included, as in HackerTube's download. Fetch the rest with a
free YouTube Data API key: `scripts/fetch_youtube_meta.py` and `scripts/add_youtube_meta.py` in the repo do it in a few
minutes, and HackerTube's `DATA.md` walks through getting a key. Thumbnails need no key:
`https://i.ytimg.com/vi/<vid>/hqdefault.jpg`.

Quoted comments are as their authors wrote them, except that email addresses and anything that looks like a password,
key or token are replaced, since people sometimes paste those into forum posts.

## How it was made

- **Hacker News**: HackerTube's dataset (comments read and labelled by Claude), plus newer comments labelled the same
  way by the weekly refresh.
- **Stack Exchange**: the public data dump of 117 sites, with the same inclusion rule as HackerTube (at least 3
  different people, in at least 3 different months, with real commentary), mentions labelled and cards written by
  Claude.
- **The open web**: every link to YouTube in Common Crawl's link data, filtered to sites people actually read, then
  judged by Qwen3-32B on one GPU. See `pipeline/README.md` in the repo. These videos come with a count of linking
  sites and a model's one-line reason, not with what those sites said.

Model-written labels and summaries are good but not perfect: expect a few percent to be off.
