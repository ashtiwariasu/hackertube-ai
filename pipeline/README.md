# The open-web pipeline

How HackerTube AI finds YouTube videos the rest of the web keeps pointing to, beyond Hacker News and Stack Exchange.
These scripts ran on one rented GPU machine (1x H100 PCIe, 26 cores, 1 TB disk, Ubuntu); they assume they live in
`~/cc` (crawl and judging), `~/llm` (the model server and HN labelling) and `~/hn` (the HN archive) on that machine.
They are research code: small, resumable, run by hand or by the chain scripts, not a packaged tool.

## 1. Read the links from Common Crawl (`crawl/`)

Common Crawl publishes WAT files: the links on every page it crawled, without the page text. `run_v2.sh CRAWL WORKERS`
streams all ~100,000 WAT files of one monthly crawl straight from `data.commoncrawl.org` through `extract_v2.py`,
which keeps every link to a YouTube video as one row: video id, linking domain, link kind (`A` or `EMBED`), page type,
anchor text and page title. Nothing is stored but those rows. A failed download is retried and never counted as done.

One crawl (CC-MAIN-2026-43) took 8.2 hours on 26 cores and gave 77.3 million (video, linking site) pairs:
58.9 million different videos across 5.4 million sites, 2.5 million of them linked from 3 or more sites.

`aggregate_box.sh CRAWL` counts sites per video. Raw link counts are not recommendations: most links are embeds on
music, fan and game sites, and link networks fake variety. `filter_full.py` drops YouTube's own sites, counts a site
that links more than 3,000 videos only once, and marks a site "reputable" when it is in the
[Tranco](https://tranco-list.eu) top 100,000. `filter_domains.py` lists likely link farms.

## 2. Look the candidates up and judge them (`judge/`)

`bf_prep.py` builds the candidate pool (videos with at least 2 or 3 reputable linking sites, across crawls), and
`fetch_meta_ids.py` reads each one's title, channel, description, length and language from the YouTube Data API with
your own key (50 videos per request; the backfill loop paces itself to 250,000 videos a day).

`web_gate.py` asks an open model (Qwen3-32B in FP8, served by vLLM, see `llm/serve.sh`) whether each video belongs in
a library of videos worth a curious adult's time, what kind of video it is (talk, lecture, explainer, documentary...),
which area it's in, whether it's in English, and why it's worth watching, in one sentence. It runs at about 10 videos
a second with 64 to 96 parallel requests. `web_topics.py` then places kept videos in the shared topic taxonomy.

Results so far: of 64,385 videos linked from 3 or more reputable sites, 11,316 were kept. A wider pass over videos
with 2 or more reputable sites kept 18%. The latest backfill looked up 1.03 million videos, judged 888,000 and kept
117,000 (13%); those aren't in the live catalogue yet.

`judge_chain.sh` and `sep_chain.sh` run steps 1 and 2 end to end for one crawl; `bf_loop.sh` runs the backfill.

## 3. Turn it into a pack (`crawl/compute_web_pack.py`)

Kept videos become `packs/web/videos.jsonl`: per video, how many independent sites linked it, across how many monthly
crawls, the top linking sites and the one-line reason (format in `packs/PACKS.md`, "Web pack").
`scripts/build_catalog.py` merges it with the community packs.

What this evidence is and isn't: it shows which sites linked a video, not what they said about it. A Hacker News or
Stack Exchange pick comes with real people's words; an open-web pick comes with a site count and a model's summary.
Finding out what the linking pages said (from the page text in Common Crawl's WARC files) is the obvious next step.

## 4. Label Hacker News comments with an open model (`llm/`, `hn/`)

`hn/fetch.sh` downloads the [open-index/hacker-news](https://huggingface.co/datasets/open-index/hacker-news) archive.
`llm/hn_filter.py` finds every comment linking a YouTube video, and `llm/hn_label_bulk.py` asks the same model what
each comment does with the video (recommends it, cites it, jokes, criticises) using the prompt in `llm/label_lib.py`.
`llm/calibrate.py` checks it against 300 comments labelled for HackerTube (`llm/gold300.json`): it agrees on
"recommendation or not" 84% of the time. 243,000 comment and video pairs took 5.7 hours. These labels are for
widening the Hacker News part of the catalogue beyond HackerTube's; they aren't in the live catalogue yet.
