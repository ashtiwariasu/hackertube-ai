# Wikipedia citation counts for YouTube videos

This lane does not produce `videos.jsonl` or `mentions.jsonl`. It produces a booster: for each YouTube video, the number of distinct English Wikipedia articles (namespace 0) that link to it.

## Source
- Dump: enwiki **20261001** (`/latest/` on dumps.wikimedia.org; files last modified 8 Oct 2026)
- `enwiki-latest-externallinks.sql.gz` (5,007,807,225 bytes) and `enwiki-latest-page.sql.gz` (2,432,679,945 bytes), both passed `pigz -t`
- Schema is post-MediaWiki 1.41: `el_id, el_from, el_to_domain_index, el_to_path`

## Method
1. Stream the externallinks dump through `pigz -dc | grep -F youtu` and parse each tuple with a regex (no MySQL). Keep rows whose `el_to_domain_index` starts with `http(s)://com.youtube.` (any subdomain: www, m, music, country), `com.youtube-nocookie.` or `be.youtu.`.
2. Get the video id from `el_to_path`: `?v=` / `&v=` / `#!v=`, `video_id=`, `/embed/`, `/v/`, `/e/`, `/shorts/`, `/live/`, `/watch/`, and `youtu.be/<id>`. An id is 11 characters and its last character must be one of `AEIMQUYcgkosw048` (true of every real YouTube id). Trailing junk from broken wiki markup (`ID--`, `IDAnd`) is tolerated: 1,318 rows.
3. Join `el_from` to the page dump, keep `page_namespace = 0`, count distinct articles per video.
4. Titles: up to 5 per video, ordered by how many distinct URLs for that video the article has, then by article length (longer first).

Script: `wiki_yt.py` (also on the H100 at `~/packs/wikipedia/`).

## Counts at each stage
| Stage | Count |
|---|---|
| externallinks rows on YouTube domains | 1,053,983 |
| ... with a parseable video id | 965,430 |
| distinct (video, page) pairs, all namespaces | 957,589 |
| distinct pages citing any video, all namespaces | 456,784 |
| page rows parsed | 66,384,916 |
| citing pages in namespace 0 (articles) | 297,672 |
| **distinct videos cited by >= 1 article** | **518,030** |

Main non-video YouTube links skipped: `/user` 19,969, `/channel` 19,020, `/playlist` 11,687, `/c` 3,547, `/watch` with no usable id 3,360, `/results` 2,539.

Citing pages by namespace: articles 297,672; User talk 54,985; Talk 45,120; User 25,455; Wikipedia 20,914; File 5,813; Draft 3,278.

youtu.be is almost absent (35 rows) because enwiki's spam blacklist blocks URL shorteners, so editors write full youtube.com links. youtube-nocookie: 12 rows.

### Articles per video (all 518,030 videos)
| Articles | Videos |
|---|---|
| 1 | 477,556 |
| 2 | 32,854 |
| 3-4 | 6,483 |
| 5-9 | 988 |
| 10-49 | 148 |
| 50+ | 1 |

`wiki_counts.json` is 33.7 MB with every video, so it was **not** trimmed (the 150 MB rule did not apply).

## Catalogue overlap (app/data/videos.json, 6,386 videos)
- **1,052 catalogue videos (16.5%) are cited by at least one article**
- 259 by 2+ articles, 17 by 5+
- Distribution: 1 article 793; 2 articles 175; 3-4 articles 67; 5-9 articles 16; 10+ articles 1
- Most cited: `9bZkp7q19f0` Gangnam Style (11: History of YouTube, Gangnam Style, ...), `EY6q5dv_B-o` (9: History of Unix, Unix, ...), `Ik1AK56FtVc` (8), `enHF9lMseP8` (8: MOS Technology 6502, Atari 2600, ...), `bRcu-ysocX4` (7: Sea Peoples, ...). The full top 25 is in `catalog_overlap.json`.

## Not in the catalogue
`top_not_in_catalog.json` lists 2,000 videos, with 155 down to 4 articles each (148 have 10+, 1,120 have 5+). **Caveat:** the externallinks table includes links that come from transcluded templates and shared citation templates. Most of the high counts come from those, not from editors picking a video for each article: one navbox video sits on 155 Carlism articles, a minor-planet naming video on 26 asteroid articles, and a team-season citation on 25 NFL season pages. A "Cited on Wikipedia" pack would need to filter these. One way is to keep only videos whose articles are topically varied, or to check the wikitext of a sample of articles.

## Files
- `wiki_counts.json`: `{vid: {"articles": n, "titles": [...]}}`, 518,030 videos
- `catalog_overlap.json`: `{"summary": {...}, "counts": {vid: articles}}` for catalogue videos only
- `top_not_in_catalog.json`: top 2,000 by article count, not in the catalogue
- `stats.json`: raw stage counters; `catalog_vids.json`: the catalogue vid list used for the join; `wiki_yt.py`: the script

## Runtime and cost
- Download: about 18 min (both files in parallel). Integrity check: about 2 min. Parse and join: 252 s (externallinks 89 s, page dump 157 s).
- No API spend, no model calls, US$0.

## Licence
Wikipedia text is CC BY-SA 4.0. This output has no article text, only which article links to which video plus article titles. Those are facts, and titles are used as short references. If the site shows "Cited on Wikipedia (n articles)" with titles, link back to the articles. That is the attribution, and it is good practice.

## H100 cleanup
The raw dumps (7.4 GB) were deleted. A new dump comes out every month, so a re-run should fetch a fresh one. Kept at `~/packs/wikipedia/` (58 MB): `yt_rows.txt.gz` (all 1.23M raw externallinks lines containing "youtu", for re-parsing without downloading again), `out/`, and the script.
