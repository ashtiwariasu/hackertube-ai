# Bluesky pack: collector report (3-Nov-2026)

Status: collector running on the H100 as a user systemd service since 01:28 UTC 3-Nov-2026.
No labelling, no cards, no API money spent (US$0). No videos.jsonl / mentions.jsonl yet: nothing can
meet the 3-month rule until January 2027 at the earliest.

## What runs where

| File | Purpose |
|---|---|
| `collector.py` | Jetstream v2 live tail of `app.bsky.feed.post` commits + `account` events into SQLite |
| `ytlinks.py` | YouTube id extraction (text, link facets, external embed incl. recordWithMedia) |
| `backfill_search.py` | one-off sample from the public search API (already run) |
| `export_pack.py` | candidate stats + ready-to-label mentions file (no API calls) |
| `bluesky-yt-collector.service` | user systemd unit |
| `status.sh` | health check |

On the box everything lives in `~/packs/bluesky/` (own venv `.venv`, db `data/bluesky.db`,
status `data/status.json`, logs `logs/collector.log` rotating 3 x 5 MB, exports `out/`).

## Key finding: endpoints changed

Jetstream is now v2. Official v2 hosts are `wss://jetstream.us-east.bsky.network` and
`wss://jetstream.us-west.bsky.network`, path `/xrpc/network.bsky.jetstream.subscribeEvents`,
subprotocol `xrpc.v1.json`. The old `jetstream2.us-east...` hosts are the legacy v1. We use v2.

- Live tail is unauthenticated and unmetered. Cursor = per-instance `seq`, inclusive, at-least-once.
- Lookback window observed: about 36 hours (floor was seq 26667671657, 1-Nov 13:00 UTC). An older
  seq cursor gets HTTP 400 `CursorTooOld`; a timestamp cursor (unix microseconds) is clamped to the floor.
- **Full history is available through "Network Replay"** (v2 archive back to the start of the network),
  but its HTTP side needs a Bluesky Jetstream API key and is metered by bytes. Creating that key needs
  a person to sign up at bsky.network, so I did not do it. With a key, the whole Bluesky post history
  could be replayed and the 3-month rule tested now instead of waiting until 2027. That is the single
  biggest lever for this pack and worth a decision.
- No compression option was used (v2 docs do not offer the v1 zstd mode; bandwidth is about 30 KB/s).

## Collector design

- Stores only posts that link a YouTube video: uri, did, rkey, cid, createdAt, seen time (server
  `witnessedAt`), month (from seen time, so backdated `createdAt` cannot fake month spread),
  text (max 1500), langs, link facets with byte spans, external embed uri/title/description,
  reply root/parent. Video ids in `post_videos(uri, vid)`.
- Deletions honoured: post delete removes the row; an edit that drops the YouTube link removes it;
  account `deleted` or `takendown` purges all that account's rows; other inactive statuses are recorded
  in `accounts` and excluded by the exporter.
- Cursor (`seq`, `time_us`, host) is committed in the same SQLite transaction as the data, once a
  second, so a crash replays at most a second and duplicates are idempotent (keyed on at:// uri).
  Failover to the other host resumes by timestamp (seq is per instance). Out-of-window restarts are
  logged in the `gaps` table.
- First start began at the retention floor, so it captured about 36 h of history for free.
- Robustness: systemd `Restart=always`, linger enabled (`sudo loginctl enable-linger ubuntu`), so it
  survives logout and reboot. Nice 10, idle IO class, MemoryMax 1G. Unexpected library errors
  (seen once: websockets/SSL `resume_reading` on Python 3.10 during fast catch-up) are caught and
  reconnect in-process. Verified: kill/restart resumed at the saved seq with no gap.
- Load: about 1-2% of one core and 45 MB RAM when live; catch-up used one core for 13 minutes. No GPU.

## First ~30 minutes of numbers (01:28 to 01:58 UTC)

- Events seen live: **about 45 frames/s** (post creates ~41/s, post deletes ~3/s, account events ~0.5/s).
  Catch-up from the 36 h floor ran at 14,000-16,000 frames/s and reached live at 01:41.
- Frames processed: 4.54 M (4.13 M post creates, 334 k post deletes, 38 k account events).
- YouTube posts in db: **191,621** (103,161 from Jetstream covering ~36 h, 88,471 from the search sample).
- YouTube share of new posts: about **64,600 YouTube posts/day** from about 28,500 distinct authors
  (last full 24 h, after deletes), roughly 2% of all posts.
- Distinct videos: **140,798** (75,043 from Jetstream alone).
- Deletions applied so far: 155 posts, 22 deleted accounts, 10 takendown accounts.
- Disk: db 220 MB for 191.6 k posts, about 1.15 KB per post incl. indexes, so
  **about 75 MB/day, about 27 GB/year**. Logs are capped at 20 MB. 883 GB free.

### Top 10 videos by distinct authors (all mentions, titles from link cards)

| vid | authors | authors with >=100 own chars | months | title |
|---|---|---|---|---|
| lJb6pOsp_lM | 132 | 8 | 1 | Major video as @senwarren outlines where Trump's bodies are buried |
| Np7_mlvXFcA | 128 | 23 | 1 | Midterms 2026: Last Week Tonight with John Oliver (HBO) |
| 6iObgyyYYuo | 124 | 10 | 2 | Woody Harrelson: "No one loves you more than Exxon" |
| Ba1rJgtDGbo | 118 | 19 | 1 | Trump Family RUINED After THIS Nuclear BOMBSHELL... |
| Zz9phTA-O5A | 110 | 26 | 1 | In America |
| D0On_NbmtDY | 109 | 17 | 1 | Jane Doe Tells Trump to Resign... |
| 0cSf7lxWOlI | 104 | 17 | 1 | BREAKING: Trump Just Threatened to Prosecute Me |
| GqPTtJNxVUQ | 99 | 15 | 1 | BREAKING: Supreme Court Delivers BOMBSHELL Blow to Trump... |
| BYkofBaifRg | 94 | 10 | 1 | Spread the word!! |
| tjwX3-P5bxU | 87 | 36 | 1 | Haley Stevens defends her endorsement switch (CNN) |

Expected for a 36 h window two days before the US midterms: news and politics dominate, and several
look like coordinated share campaigns. The 3-month rule plus stance labelling should filter these;
the rule exists exactly for this.

## Exporter (`export_pack.py`)

Counts per video: mentions, distinct authors (did), distinct months, and the same counted only over
mentions with >= 100 chars of the author's own text (`q_*`). "Own text" strips links, hashtags,
@mentions, "via @YouTube" and a pasted copy of the video title, because auto-shares
("<title> via @YouTube #tags") are a large share of Bluesky YouTube posts and are not the author's words.
Mention text follows the PACKS.md shape: links replaced by `[VIDEO]` (this video) or `[LINK]` using
facet byte spans, then a regex pass for leftover URLs.

Funnel right now: 140,721 videos; 15,199 with >= 2 authors; 6,780 with >= 3 authors; 563 with >= 3
qualifying authors; 103 with >= 3 qualifying authors over 2 months; **0 meet the rule (needs 3 months)**.

Outputs on the box (`~/packs/bluesky/out/`): `candidates.jsonl` (videos with >= 2 authors),
`mentions_to_label.jsonl` (563 videos, 2,737 mentions, up to 40 per video, one per author first, so the
final 20 can favour recommendations after labelling), `export_summary.json` (copied here).

## Search backfill (one-off, done)

`api.bsky.app/xrpc/app.bsky.feed.searchPosts` with `domain=youtube.com` and `domain=youtu.be`,
`sort=latest`, `limit=100`. Limits hit:
- `public.api.bsky.app` returns 403 for search.
- On `api.bsky.app`, any request with a `cursor` returns 403 "Request forbidden by administrative rules",
  so unauthenticated search gives one page (100 posts) per query. `hitsTotal` is capped at 10000.
- `since`/`until` work (even for 2025). I took one page per 1-hour window per domain for 21 days
  (1,008 requests, 1/s, all HTTP 200, no 429). 481 of 1,008 windows were full, so this is a sample,
  roughly half of each hour, not full coverage. I did not walk `until` backwards to imitate paging,
  since that would sidestep the deliberate paging block.
- Result: 99,783 posts returned, 88,471 kept (YouTube id found), 70,224 distinct videos, covering
  13-Oct to 3-Nov. Months from search use `indexedAt`.

Deeper history: authenticated search (app password) allows paging, and Jetstream Network Replay with an
API key gives the full archive. Both need a human to create credentials.

## Operating it

```
ssh your-gpu-box
~/packs/bluesky/status.sh                                   # active?, restarts, status.json, disk, recent warnings
export XDG_RUNTIME_DIR=/run/user/$(id -u)
systemctl --user status bluesky-yt-collector
journalctl --user -u bluesky-yt-collector -n 50
systemctl --user stop bluesky-yt-collector                  # stop (cursor is saved; restart resumes within ~36 h)
systemctl --user disable --now bluesky-yt-collector         # stop and do not start on boot
systemctl --user start bluesky-yt-collector                 # start again
cd ~/packs/bluesky && .venv/bin/python export_pack.py       # refresh out/ (safe while collector runs)
```

If the collector is down for more than ~36 h, it restarts from the retention floor and records the gap
in the `gaps` table.

## Licence and policy notes

- Bluesky posts are public, but the authors own their content. The Bluesky developer guidelines require
  honouring deletions; the collector does this for posts and for deleted or taken-down accounts, and the
  exporter drops other inactive accounts. Published pack data should be re-checked against deletions at
  export time (the db is kept current, so exporting from it does this).
- Keep quotes short and link back to the post (`url` field uses bsky.app/profile/<did>/post/<rkey>).
- Authors are stored as DIDs, not handles.

## Problems / open items

1. Decide on a Jetstream API key for Network Replay (full history now instead of waiting to 2027).
2. Political share campaigns dominate the current window; stance labelling and the month rule should
   handle them, but a per-author daily cap may be worth adding at export time.
3. `CPUQuota` is not enforced in the user slice (cgroup delegation); it only matters during catch-up.
