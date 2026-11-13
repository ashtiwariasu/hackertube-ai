# HackerTube AI

The YouTube videos that Hacker News, Stack Overflow and the open web keep recommending, with a reason on every pick
and a personal shortlist on top. It runs at [hackertube.ai](https://hackertube.ai).

It's built on [HackerTube](https://hackertube.org), the library of videos Hacker News keeps telling each other to
watch, and on its open dataset. HackerTube AI is an independent project, not affiliated with hackertube.org, Andrej
Karpathy, Hacker News, Y Combinator, YouTube or Google.

## What it adds to HackerTube

- **More places that vouch for videos.** HackerTube reads Hacker News (6,454 videos). This adds 117 Stack Exchange
  sites (about 5,400 videos) and the open web: every link to YouTube in Common Crawl, filtered to sites people
  actually read and judged by an open model on one GPU (35,500 videos). 47,126 in all. The pipeline is in
  [`pipeline/`](pipeline/README.md).
- **It gets to know you without twenty likes first.** Connect GitHub, Hacker News, Bluesky, Mastodon, Stack Overflow,
  a personal site or YouTube; paste any link or a few words; upload a screenshot of your YouTube history; or paste what
  ChatGPT or Claude says about you. Nothing is stored until you sign in and keep it.
- **A personal shortlist that says why.** Ranked by the best match to any one of your interests, with creators you
  follow in a few slots and popularity only breaking ties (`Catalog.librarian` in `app/catalog.py`).
- **A player that learns.** Watching through the embedded player, "Seen it" and "Not for me" all steer what comes
  next. A watch only counts as real when enough of the video was actually played.
- **Reviews and a Partner Program (optional, off by default).** Viewers who really watched a video can say whether
  it's worth it; reviewers can be paid, with every paid review checked by independent unpaid viewers.

## Try it in two minutes

The repo ships a demo catalogue of 1,800 of the best-scored videos, so it runs with nothing to download but its
Python packages and, on the first search, the small embedding model.

```bash
git clone https://github.com/ashtiwariasu/hackertube-ai && cd hackertube-ai
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
DEV_LOGIN=1 .venv/bin/uvicorn app.main:app --port 8080
```

Open http://localhost:8080. Users are kept in memory. `/dev/login?uid=me` signs you in as a test user (only when
`DEV_LOGIN=1`), so you can try connecting sources and the personal feed without Google sign-in.

Run the tests with `.venv/bin/pip install pytest && .venv/bin/python -m pytest -q`.

## The full catalogue

The full catalogue is published as a data release (see [Releases](https://github.com/ashtiwariasu/hackertube-ai/releases)
and [DATA.md](DATA.md)) in two forms:

- `app-data`: this app's own files. Unzip its `app-data` folder over `app/data` and restart.
- `hackertube-format`: the same files and fields as HackerTube's own download, plus the new sources, so anything
  built on HackerTube's data (TV apps, translations, newsletters) can use it unchanged.

YouTube's API terms don't allow redistributing its metadata, so the release has no durations, view counts or publish
dates. The site works without them; to add them, get a free YouTube Data API key and run:

```bash
YOUTUBE_API_KEY=... .venv/bin/python -I scripts/fetch_youtube_meta.py app/data/videos.json youtube_meta.jsonl
.venv/bin/python -I scripts/add_youtube_meta.py youtube_meta.jsonl
```

## How it works

- `app/`: a FastAPI app with server-rendered Jinja pages (fast first paint, every page indexable). The whole catalogue
  lives in memory, about 10 KB per video, with float16 embedding matrices.
- Search and "similar" use [bge-small-en-v1.5](https://huggingface.co/BAAI/bge-small-en-v1.5) via fastembed,
  the same model as HackerTube's data.
- Ranking: a video's standing comes from how many separate months people recommended it, in each community, with a
  smaller bonus for independent websites linking it. YouTube view counts don't raise a video's standing.
- Users, reviews and the Partner Program are in Firestore when deployed (`STORE=firestore`), in memory otherwise.
- `scripts/build_catalog.py` merges HackerTube's data with the community packs in `packs/` (format in
  [`packs/PACKS.md`](packs/PACKS.md)); `app/refresh.py` is the weekly job that adds new Hacker News picks.
- Some features use Claude: reading screenshots of your history, the "Ask your AI" import, and checking reviews
  before they're shown. Without `ANTHROPIC_API_KEY` the first two don't work and new reviews wait unpublished.

## Deploy your own

`deploy.sh` deploys to Google Cloud Run from source. Copy `deploy.env.example` to `deploy.env` and fill it in; it lists
every setting and the secrets it reads from Secret Manager. You'll want:

- a Google Cloud project with Cloud Run, Firestore and Secret Manager, and a `hackertube-session-secret` secret;
- a Google OAuth client for sign-in (`scripts/set-oauth.sh`) and a YouTube Data API key for the YouTube source;
- optionally Stripe (a one-time price for Pro), PostHog or Google Analytics, and an Anthropic API key.

The Privacy and Terms pages describe hackertube.ai's own service; rewrite them for yours, and set `OPERATOR_NAME`,
`OPERATOR_PLACE` and `OPERATOR_CONTACT`.

A site that pays reviewers can keep its own anti-abuse checks private in `app/rules_private.py`, which replaces the
public ones in `app/notes.py` when present.

## Contributing

Issues and pull requests are welcome. Keep changes small, add a test, and run the suite before sending. New community
packs (another forum, a newsletter archive, a subreddit's own export) are the most useful contribution; follow
`packs/PACKS.md`.

## Licence and credits

Code: [MIT](LICENSE). Data: see [DATA.md](DATA.md) and [LICENSE-DATA.md](LICENSE-DATA.md).

The Hacker News part of the catalogue comes from [HackerTube](https://hackertube.org)'s dataset (ODC-By 1.0),
derived from the [open-index/hacker-news](https://huggingface.co/datasets/open-index/hacker-news) archive; Hacker
News data is provided by Y Combinator and comments remain their authors' work. Stack Exchange content is CC BY-SA 4.0,
with every quoted post linked to its author. The open-web counts are derived from [Common Crawl](https://commoncrawl.org).
Video titles and channel names belong to their creators; video data comes from the YouTube Data API.
