# Stack Exchange pack: report

Built 3 November 2026. Communities: `se:<site>` for every non-meta Stack Exchange site (181 sites scanned; 117 have at least one video in the pack).
Output: `videos.jsonl` (5,873 video-community lines, 5,679 distinct videos; 164 videos qualify in more than one community and get one line per community), `mentions.jsonl` (31,977 labelled mentions, max 20 per video line).

## Source data and licence

- Data: the community mirror of the Stack Exchange data dump to 2026-03-31, Hugging Face dataset `flymin/stackexchange_20260331` (one 7z per site; 181 non-meta sites, 91 GB). Meta sites were skipped. Only `Comments.xml`, `Posts.xml` and `Users.xml` were read, streamed straight out of each 7z in one pass (nothing written uncompressed). The archives were deleted from the H100 after extraction.
- Licence: user content is CC BY-SA (2.5, 3.0 or 4.0 depending on post date, per the `ContentLicense` attribute). Display requires attribution: every mention carries the author's display name and a link to the post or comment (`https://<site>/q/<id>`, `/a/<id>`, `/posts/comments/<id>`).
- The official dump download page asks that the dump not be used to train an LLM. This pack does not train anything: the text is only labelled by Claude (inference) and displayed with attribution.
- Watermarks: the community list (LunarWatcher/se-data-dump-transformer, `docs/meta/Known watermarks.md`) documents one watermark, in the 2025-06-30 dump only: two fabricated posts per site with Ids 1000000001 and 1000000010, owned by the Community user (-1). No watermark is listed for later dumps. As a guard the extractor drops those two Ids, any post with Id >= 1,000,000,000, and anything owned by the Community user. Result: 0 watermark posts and 0 Community-user posts carried a YouTube link in this dump; 9 links were dropped for having no author.

## Method

1. Extraction (`scripts/02_extract.py`): every question, answer and comment whose own prose links a YouTube video (youtube.com watch/embed/v/shorts/live, youtu.be, youtube-nocookie, m.youtube, attribution links). Links inside code (`<pre>`, `<code>`, backticks in comments) are ignored: on Stack Overflow these are sample URLs in embed code or URL-parsing regexes, not pointers to a video (this removed about 17,000 SO question and answer links). Pseudo ids `videoseries` and `live_stream` are ignored. Author = `OwnerUserId` / `UserId` (display name from Users.xml) or, for deleted accounts, `OwnerDisplayName` / `UserDisplayName`; posts and comments with neither are skipped. Month = `CreationDate`. Context = the question title (for answers and comments, the parent question's title). Text = HTML stripped, this video's links replaced by `[VIDEO]`, other links by `[LINK]` (descriptive anchor text is kept before the token).
2. Inclusion rule (`scripts/04_candidates.py`), applied per community: a mention counts when the author's own text is at least 100 characters (tokens removed, and text inside `<blockquote>` not counted as the author's own); a video is a candidate when its counted mentions come from at least 3 distinct authors in at least 3 distinct calendar months.
3. Stance labels (`scripts/05_label.py`): every counted mention of every candidate (41,386) labelled by `claude-haiku-5-5` (Batches, structured output, thinking off, effort low): stance, a why of up to 15 words, a verbatim quote (kept only if it occurs in the text), and a `sample_url` flag for links used only as example data (152 flagged and dropped).
4. Qualification after labelling (`scripts/06_cards.py qualify`): the same 3 authors / 3 months rule, counting only mentions labelled strong_rec, rec or reference (a video cannot qualify on jokes or criticism alone). Stats in `videos.jsonl` are over all counted mentions of the video in that community; `score = rec_months + 0.5 * months with a strong_rec`.
5. YouTube metadata (`videos.list`, 127 calls): videos missing (deleted), not public, not embeddable or not processed are dropped.
6. Cards (`claude-sonnet-5-5` Batches, structured output, no thinking, effort medium): title, channel, description (first 1,500 chars), duration, community name and up to 20 mentions (700 chars each), with the 150 taxonomy ids and names in a cached system prompt; categories validated against `app/data/taxonomy.json`. The video-level quote is kept only when it occurs verbatim in the named mention. Up to 20 mentions per video line are written, one per distinct author first (best stance, then post score), then the rest.

## Counts

| stage | all sites | Stack Overflow | rest |
|---|---|---|---|
| question bodies linking YouTube (prose) | 100,053 | 41,349 | 58,704 |
| answers linking YouTube | 134,946 | 46,531 | 88,415 |
| comments linking YouTube | 107,937 | 37,677 | 70,260 |
| mention rows (post or comment x video) | 382,621 | 132,667 | 249,954 |
| distinct (community, video) | 296,645 | 86,664 | 209,981 |
| mentions with >= 100 chars own text | 327,488 | 114,400 | 213,088 |
| candidates: 3+ authors in 3+ months | 6,678 | 4,085 | 2,593 |
| candidate mentions labelled | 41,386 | 27,600 | 13,786 |
| qualified after stance | 6,523 | 4,004 | 2,519 |
| with usable YouTube video | 5,877 | 3,544 | 2,333 |
| cards written (final `videos.jsonl`) | 5,873 | 3,541 | 2,332 |
| mentions written | 31,977 | 19,837 | 12,140 |

YouTube dropped 639 of 6,322 distinct qualified videos (deleted, private or not embeddable). Four cards were refused by Sonnet's cyber safety classifier on two attempts (se:superuser FW-RLkzjAS8; se:stackoverflow 8BiOPBsXh0g, O0C4V6JmlNw, hJ8IwyhqzD4) and are left out.

Per-site table (every site, sorted by mention rows; "posts" and "comments" are all rows in Posts.xml and Comments.xml; "videos 3+ authors" counts authors over mentions with >= 100 chars):

| site | posts | comments | q w/ YT | a w/ YT | c w/ YT | mention rows | distinct videos | >=100 chars | videos 3+ authors | candidates (3 authors, 3 months) | candidate mentions labelled | qualified after stance | final videos (YouTube ok + card) | mentions written |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **all sites** | 83614065 | 133125178 | 100053 | 134946 | 107937 | 382621 | 296645 | 327488 | 7227 | 6678 | 41386 | 6523 | 5873 | 31977 |
| se:stackoverflow | 60349494 | 91268612 | 41349 | 46531 | 37677 | 132667 | 86664 | 114400 | 4337 | 4085 | 27600 | 4004 | 3541 | 19837 |
| se:scifi | 226874 | 576690 | 2036 | 8159 | 4484 | 16968 | 14514 | 13393 | 178 | 143 | 579 | 133 | 109 | 445 |
| se:blender | 237361 | 413107 | 5179 | 2434 | 4580 | 13310 | 9346 | 11126 | 422 | 393 | 2103 | 393 | 346 | 1834 |
| se:physics | 614376 | 1220182 | 3420 | 3518 | 4567 | 12777 | 8474 | 10649 | 390 | 369 | 2292 | 359 | 346 | 2028 |
| se:math | 3932624 | 7433794 | 4110 | 2284 | 4339 | 11598 | 7772 | 9086 | 350 | 340 | 2035 | 332 | 312 | 1860 |
| se:music | 98340 | 168599 | 2123 | 3021 | 2058 | 9705 | 8768 | 8871 | 66 | 58 | 246 | 56 | 52 | 213 |
| se:worldbuilding | 224965 | 640848 | 543 | 4038 | 3367 | 9308 | 7715 | 7851 | 151 | 139 | 673 | 125 | 120 | 604 |
| se:movies | 76605 | 120231 | 1394 | 4980 | 1531 | 9210 | 8647 | 7871 | 19 | 11 | 35 | 10 | 10 | 32 |
| se:gaming | 278664 | 341453 | 1292 | 4554 | 1490 | 8641 | 7997 | 7616 | 35 | 21 | 86 | 21 | 21 | 86 |
| se:electronics | 573661 | 1411083 | 2513 | 2158 | 3110 | 8627 | 6673 | 7344 | 159 | 146 | 857 | 144 | 142 | 804 |
| se:superuser | 1275901 | 1957951 | 1655 | 2525 | 1646 | 6333 | 5724 | 5482 | 23 | 23 | 82 | 20 | 16 | 58 |
| se:english | 434879 | 979056 | 1105 | 1431 | 2400 | 5600 | 5133 | 4218 | 13 | 12 | 51 | 9 | 7 | 34 |
| se:aviation | 80070 | 192836 | 1019 | 1630 | 1540 | 4808 | 4030 | 4075 | 68 | 55 | 229 | 53 | 51 | 214 |
| se:space | 51453 | 150470 | 1232 | 1384 | 1388 | 4689 | 3411 | 4131 | 107 | 75 | 411 | 74 | 67 | 383 |
| se:askubuntu | 970914 | 1589970 | 1449 | 1656 | 984 | 4317 | 3583 | 3735 | 80 | 71 | 320 | 70 | 61 | 272 |
| se:diy | 243712 | 512759 | 851 | 1657 | 1263 | 4262 | 3323 | 3716 | 22 | 21 | 208 | 20 | 18 | 96 |
| se:ell | 293773 | 518154 | 1801 | 871 | 1195 | 4132 | 3691 | 3465 | 6 | 5 | 18 | 3 | 3 | 10 |
| se:gamedev | 142240 | 239563 | 1503 | 1170 | 939 | 4107 | 3603 | 3755 | 42 | 41 | 169 | 41 | 39 | 160 |
| se:ru.stackoverflow | 988214 | 1913920 | 944 | 1021 | 1361 | 3554 | 3153 | 2566 | 18 | 17 | 80 | 16 | 14 | 70 |
| se:musicfans | 6219 | 6040 | 721 | 827 | 199 | 3034 | 2888 | 2819 | 5 | 3 | 10 | 3 | 3 | 10 |
| se:skeptics | 27760 | 129394 | 1050 | 683 | 721 | 2958 | 2738 | 2615 | 6 | 3 | 11 | 3 | 3 | 11 |
| se:bicycles | 74296 | 172870 | 435 | 1051 | 774 | 2615 | 2060 | 2272 | 40 | 39 | 229 | 38 | 38 | 218 |
| se:politics | 62437 | 220077 | 530 | 828 | 774 | 2509 | 2253 | 2236 | 17 | 13 | 67 | 13 | 12 | 64 |
| se:stats | 441464 | 862595 | 771 | 791 | 662 | 2431 | 1933 | 2175 | 39 | 37 | 167 | 37 | 33 | 149 |
| se:philosophy | 92630 | 275032 | 335 | 887 | 852 | 2380 | 1848 | 2125 | 13 | 11 | 64 | 9 | 9 | 58 |
| se:mechanics | 70561 | 119837 | 642 | 754 | 600 | 2271 | 2095 | 1989 | 7 | 5 | 17 | 5 | 5 | 17 |
| se:fitness | 30148 | 45402 | 271 | 840 | 328 | 2179 | 1894 | 2030 | 13 | 12 | 53 | 12 | 11 | 45 |
| se:apple | 329678 | 480265 | 510 | 888 | 396 | 1919 | 1730 | 1695 | 11 | 11 | 42 | 10 | 10 | 37 |
| se:graphicdesign | 103733 | 152785 | 422 | 726 | 526 | 1840 | 1692 | 1575 | 6 | 5 | 20 | 5 | 5 | 20 |
| se:robotics | 102859 | 30274 | 655 | 661 | 176 | 1751 | 1508 | 1671 | 20 | 18 | 82 | 18 | 17 | 78 |
| se:es.stackoverflow | 419679 | 711775 | 429 | 570 | 682 | 1746 | 1473 | 1453 | 3 | 3 | 10 | 3 | 3 | 10 |
| se:cooking | 94840 | 147860 | 386 | 693 | 490 | 1738 | 1620 | 1509 | 7 | 3 | 9 | 3 | 3 | 9 |
| se:anime | 34329 | 35129 | 359 | 808 | 200 | 1668 | 1579 | 1432 | 1 | 1 | 4 | 1 | 1 | 4 |
| se:sound | 36553 | 35217 | 522 | 589 | 205 | 1587 | 1502 | 1379 | 5 | 4 | 14 | 3 | 2 | 7 |
| se:chemistry | 103143 | 198594 | 375 | 513 | 501 | 1573 | 1390 | 1355 | 11 | 11 | 42 | 11 | 11 | 42 |
| se:photo | 97159 | 173077 | 224 | 755 | 406 | 1571 | 1371 | 1386 | 10 | 10 | 57 | 10 | 8 | 49 |
| se:martialarts | 9827 | 14771 | 222 | 579 | 239 | 1571 | 1409 | 1454 | 4 | 1 | 4 | 1 | 1 | 4 |
| se:gis | 359848 | 521460 | 345 | 692 | 415 | 1531 | 1289 | 1258 | 15 | 14 | 58 | 14 | 13 | 53 |
| se:christianity | 63792 | 158321 | 241 | 519 | 377 | 1528 | 1288 | 1383 | 4 | 3 | 23 | 3 | 3 | 23 |
| se:history | 46152 | 144296 | 310 | 462 | 511 | 1524 | 1395 | 1334 | 5 | 3 | 9 | 3 | 3 | 9 |
| se:softwareengineering | 248561 | 557486 | 173 | 644 | 535 | 1511 | 1090 | 1256 | 33 | 28 | 221 | 27 | 25 | 167 |
| se:security | 192979 | 365236 | 288 | 587 | 490 | 1466 | 1165 | 1265 | 24 | 21 | 121 | 19 | 18 | 109 |
| se:japanese | 75285 | 108333 | 579 | 297 | 315 | 1457 | 1362 | 1277 | 1 | 0 | 0 | 0 | 0 | 0 |
| se:judaism | 113545 | 319082 | 240 | 440 | 576 | 1424 | 1230 | 1054 | 4 | 3 | 11 | 3 | 3 | 11 |
| se:avp | 21232 | 23004 | 581 | 442 | 179 | 1403 | 1356 | 1291 | 2 | 2 | 6 | 2 | 2 | 6 |
| se:biology | 63878 | 113195 | 413 | 448 | 316 | 1377 | 1256 | 1234 | 7 | 6 | 18 | 6 | 6 | 18 |
| se:rpg | 170306 | 299858 | 134 | 679 | 339 | 1346 | 1159 | 1193 | 20 | 16 | 86 | 16 | 15 | 83 |
| se:engineering | 40604 | 76891 | 364 | 403 | 403 | 1341 | 1263 | 1154 | 2 | 2 | 7 | 2 | 2 | 7 |
| se:puzzling | 99775 | 261811 | 173 | 422 | 455 | 1312 | 1188 | 994 | 2 | 1 | 18 | 1 | 1 | 18 |
| se:retrocomputing | 24228 | 91931 | 208 | 437 | 450 | 1290 | 1146 | 1111 | 6 | 5 | 23 | 5 | 5 | 23 |
| se:mathoverflow | 367407 | 887557 | 229 | 405 | 512 | 1287 | 1065 | 1013 | 19 | 18 | 69 | 18 | 18 | 69 |
| se:hinduism | 44393 | 115003 | 348 | 341 | 369 | 1277 | 1146 | 1077 | 6 | 5 | 18 | 5 | 5 | 18 |
| se:astronomy | 38693 | 88172 | 330 | 356 | 423 | 1249 | 1023 | 1057 | 9 | 8 | 39 | 8 | 7 | 36 |
| se:pt.stackoverflow | 347097 | 585124 | 241 | 419 | 479 | 1248 | 1146 | 1008 | 3 | 2 | 6 | 2 | 2 | 6 |
| se:unix | 615059 | 1038595 | 403 | 406 | 300 | 1192 | 1069 | 1051 | 7 | 7 | 31 | 7 | 7 | 31 |
| se:android | 132401 | 180631 | 286 | 507 | 280 | 1164 | 1078 | 1010 | 2 | 1 | 3 | 1 | 1 | 3 |
| se:raspberrypi | 88488 | 156799 | 301 | 410 | 291 | 1122 | 965 | 992 | 9 | 8 | 29 | 8 | 8 | 29 |
| se:travel | 135873 | 321109 | 134 | 411 | 448 | 1091 | 1025 | 830 | 4 | 2 | 9 | 2 | 2 | 9 |
| se:buddhism | 40088 | 50397 | 122 | 520 | 203 | 1090 | 862 | 988 | 9 | 8 | 50 | 8 | 7 | 47 |
| se:codereview | 207101 | 351821 | 227 | 425 | 336 | 1054 | 715 | 948 | 20 | 19 | 187 | 19 | 17 | 119 |
| se:serverfault | 856021 | 1081979 | 253 | 469 | 264 | 1037 | 929 | 879 | 6 | 6 | 28 | 5 | 5 | 22 |
| se:3dprinting | 15977 | 26622 | 210 | 411 | 172 | 959 | 791 | 888 | 13 | 11 | 47 | 11 | 11 | 47 |
| se:chess | 32853 | 46853 | 167 | 375 | 200 | 945 | 837 | 807 | 5 | 5 | 19 | 4 | 4 | 16 |
| se:german | 60455 | 130024 | 233 | 207 | 352 | 916 | 852 | 732 | 1 | 1 | 3 | 1 | 1 | 3 |
| se:sports | 16268 | 15897 | 200 | 349 | 172 | 905 | 859 | 812 | 1 | 1 | 3 | 1 | 1 | 3 |
| se:law | 80783 | 180056 | 355 | 162 | 294 | 900 | 791 | 774 | 4 | 3 | 30 | 3 | 2 | 25 |
| se:outdoors | 22944 | 45842 | 103 | 360 | 245 | 890 | 845 | 770 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:drupal | 204786 | 250787 | 101 | 521 | 196 | 888 | 524 | 750 | 28 | 26 | 104 | 26 | 17 | 65 |
| se:french | 43615 | 78797 | 240 | 217 | 213 | 858 | 809 | 763 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:islam | 40072 | 47550 | 152 | 328 | 190 | 832 | 766 | 729 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:arduino | 59540 | 127925 | 270 | 230 | 242 | 802 | 721 | 694 | 3 | 3 | 11 | 3 | 3 | 11 |
| se:salesforce | 296513 | 402737 | 133 | 437 | 193 | 801 | 630 | 697 | 14 | 13 | 63 | 12 | 8 | 33 |
| se:ethereum | 119428 | 125312 | 242 | 340 | 123 | 751 | 545 | 682 | 20 | 18 | 128 | 18 | 17 | 100 |
| se:datascience | 79937 | 82620 | 177 | 350 | 131 | 743 | 622 | 676 | 13 | 13 | 52 | 12 | 12 | 49 |
| se:webmasters | 96813 | 122630 | 72 | 459 | 155 | 732 | 410 | 666 | 44 | 41 | 228 | 41 | 40 | 225 |
| se:ux | 120327 | 169941 | 88 | 332 | 220 | 712 | 642 | 607 | 5 | 5 | 17 | 5 | 3 | 11 |
| se:sharepoint | 239484 | 250605 | 115 | 396 | 131 | 698 | 565 | 604 | 9 | 9 | 35 | 9 | 8 | 32 |
| se:cs | 109611 | 201396 | 257 | 187 | 179 | 691 | 609 | 612 | 5 | 5 | 25 | 5 | 5 | 25 |
| se:ai | 28646 | 30129 | 210 | 276 | 107 | 665 | 575 | 625 | 6 | 6 | 26 | 6 | 6 | 26 |
| se:hermeneutics | 72779 | 131493 | 48 | 274 | 193 | 648 | 476 | 554 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:dsp | 66316 | 137296 | 201 | 224 | 156 | 640 | 546 | 580 | 3 | 2 | 26 | 2 | 2 | 23 |
| se:linguistics | 30121 | 78073 | 148 | 137 | 186 | 634 | 591 | 573 | 1 | 1 | 3 | 1 | 1 | 3 |
| se:codegolf | 227034 | 380749 | 233 | 133 | 233 | 629 | 558 | 446 | 4 | 4 | 26 | 4 | 4 | 24 |
| se:chinese | 43545 | 55274 | 220 | 178 | 114 | 624 | 587 | 548 | 1 | 0 | 0 | 0 | 0 | 0 |
| se:academia | 158788 | 391942 | 70 | 242 | 271 | 620 | 544 | 481 | 9 | 9 | 33 | 9 | 9 | 33 |
| se:mathematica | 238194 | 539276 | 175 | 100 | 276 | 609 | 527 | 449 | 6 | 5 | 18 | 5 | 5 | 18 |
| se:cogsci | 17399 | 27126 | 177 | 137 | 126 | 607 | 547 | 567 | 2 | 2 | 6 | 2 | 2 | 6 |
| se:writers | 54569 | 79824 | 94 | 250 | 167 | 594 | 554 | 520 | 3 | 3 | 13 | 3 | 3 | 13 |
| se:woodworking | 12394 | 29061 | 100 | 241 | 147 | 582 | 529 | 528 | 4 | 3 | 9 | 3 | 3 | 9 |
| se:matheducators | 17847 | 47076 | 49 | 229 | 164 | 582 | 532 | 504 | 2 | 2 | 8 | 2 | 2 | 8 |
| se:workplace | 147808 | 383657 | 17 | 221 | 293 | 559 | 474 | 381 | 3 | 3 | 26 | 3 | 3 | 26 |
| se:tex | 629692 | 1395460 | 133 | 151 | 249 | 558 | 439 | 419 | 7 | 6 | 25 | 6 | 6 | 25 |
| se:softwarerecs | 49176 | 60735 | 128 | 287 | 86 | 557 | 533 | 513 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:spanish | 30296 | 49355 | 166 | 139 | 140 | 554 | 523 | 498 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:webapps | 87260 | 75396 | 164 | 225 | 100 | 534 | 508 | 464 | 1 | 0 | 0 | 0 | 0 | 0 |
| se:bitcoin | 77294 | 86042 | 118 | 254 | 87 | 498 | 384 | 436 | 15 | 13 | 64 | 13 | 12 | 57 |
| se:quantumcomputing | 29148 | 40252 | 143 | 175 | 94 | 491 | 389 | 454 | 7 | 6 | 31 | 6 | 5 | 28 |
| se:gardening | 46060 | 63040 | 97 | 201 | 142 | 487 | 446 | 406 | 1 | 1 | 4 | 1 | 1 | 4 |
| se:bricks | 12954 | 14597 | 93 | 232 | 88 | 482 | 446 | 422 | 2 | 2 | 8 | 2 | 2 | 8 |
| se:earthscience | 17222 | 33940 | 102 | 144 | 167 | 473 | 443 | 400 | 1 | 1 | 3 | 1 | 1 | 3 |
| se:dba | 249682 | 351425 | 91 | 244 | 108 | 467 | 393 | 420 | 2 | 2 | 6 | 2 | 2 | 6 |
| se:crypto | 72379 | 147211 | 126 | 144 | 146 | 444 | 364 | 385 | 4 | 4 | 12 | 4 | 4 | 12 |
| se:crafts | 7292 | 10327 | 75 | 191 | 66 | 426 | 413 | 412 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:pets | 21894 | 27159 | 59 | 194 | 71 | 426 | 394 | 393 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:literature | 23530 | 24953 | 108 | 140 | 96 | 410 | 380 | 367 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:russian | 18367 | 32297 | 127 | 106 | 106 | 409 | 358 | 354 | 1 | 1 | 3 | 1 | 1 | 3 |
| se:economics | 36403 | 57358 | 139 | 106 | 113 | 394 | 359 | 344 | 2 | 2 | 9 | 2 | 2 | 9 |
| se:parenting | 30472 | 53662 | 24 | 185 | 104 | 394 | 374 | 355 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:lifehacks | 15209 | 20039 | 25 | 209 | 99 | 387 | 366 | 312 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:wordpress | 262849 | 384320 | 99 | 123 | 114 | 355 | 323 | 308 | 1 | 1 | 5 | 0 | 0 | 0 |
| se:money | 116215 | 237177 | 75 | 99 | 147 | 346 | 319 | 273 | 1 | 0 | 0 | 0 | 0 | 0 |
| se:health | 15266 | 26649 | 107 | 102 | 73 | 333 | 302 | 296 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:ham | 15130 | 27363 | 82 | 105 | 89 | 311 | 272 | 274 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:magento | 260677 | 370194 | 56 | 143 | 79 | 289 | 250 | 223 | 1 | 1 | 3 | 1 | 1 | 3 |
| se:languagelearning | 4228 | 4979 | 32 | 96 | 19 | 266 | 251 | 255 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:boardgames | 42450 | 57422 | 46 | 112 | 77 | 264 | 244 | 224 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:portuguese | 7329 | 18580 | 47 | 71 | 78 | 259 | 244 | 230 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:hardwarerecs | 9418 | 14602 | 37 | 99 | 56 | 240 | 227 | 228 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:sqa | 37195 | 39270 | 7 | 161 | 24 | 221 | 174 | 205 | 2 | 1 | 3 | 1 | 1 | 3 |
| se:hsm | 12180 | 26231 | 81 | 53 | 67 | 218 | 206 | 195 | 1 | 1 | 4 | 1 | 1 | 4 |
| se:latin | 17605 | 32232 | 58 | 41 | 67 | 213 | 185 | 185 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:computergraphics | 8609 | 14828 | 83 | 50 | 44 | 207 | 196 | 192 | 1 | 1 | 5 | 1 | 1 | 5 |
| se:quant | 51726 | 79249 | 46 | 82 | 47 | 202 | 168 | 178 | 1 | 1 | 3 | 1 | 1 | 3 |
| se:reverseengineering | 21636 | 29494 | 47 | 80 | 37 | 196 | 178 | 183 | 1 | 1 | 3 | 1 | 1 | 3 |
| se:cardano | 6864 | 5182 | 33 | 96 | 31 | 181 | 130 | 164 | 9 | 6 | 23 | 6 | 6 | 23 |
| se:cseducators | 6598 | 14135 | 13 | 88 | 37 | 175 | 157 | 164 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:cstheory | 31319 | 74494 | 31 | 66 | 57 | 174 | 150 | 146 | 1 | 1 | 5 | 1 | 1 | 5 |
| se:networkengineering | 44895 | 72409 | 38 | 59 | 64 | 174 | 144 | 145 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:ukrainian | 5915 | 8191 | 51 | 76 | 24 | 169 | 155 | 154 | 1 | 1 | 5 | 1 | 0 | 0 |
| se:korean | 5501 | 5888 | 50 | 63 | 31 | 169 | 150 | 150 | 1 | 1 | 3 | 1 | 1 | 3 |
| se:pm | 26409 | 27979 | 11 | 113 | 29 | 163 | 116 | 148 | 3 | 3 | 27 | 3 | 3 | 27 |
| se:homebrew | 20774 | 23396 | 31 | 80 | 32 | 160 | 146 | 148 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:interpersonal | 18854 | 35358 | 11 | 83 | 58 | 159 | 151 | 134 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:sustainability | 6937 | 11281 | 29 | 66 | 39 | 157 | 149 | 141 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:rus | 70977 | 91537 | 33 | 45 | 60 | 152 | 145 | 123 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:coffee | 4661 | 5055 | 19 | 89 | 26 | 151 | 123 | 140 | 4 | 4 | 20 | 4 | 4 | 20 |
| se:ja.stackoverflow | 70296 | 77793 | 76 | 33 | 25 | 144 | 130 | 133 | 1 | 0 | 0 | 0 | 0 | 0 |
| se:italian | 9797 | 23834 | 39 | 42 | 43 | 142 | 133 | 129 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:emacs | 62195 | 104994 | 48 | 44 | 41 | 138 | 121 | 118 | 2 | 2 | 6 | 2 | 2 | 6 |
| se:genealogy | 11059 | 15580 | 3 | 87 | 9 | 137 | 81 | 135 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:scicomp | 26779 | 56449 | 33 | 43 | 40 | 133 | 124 | 124 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:drones | 2709 | 2605 | 33 | 60 | 17 | 129 | 121 | 117 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:substrate | 11064 | 9860 | 19 | 71 | 18 | 126 | 85 | 112 | 5 | 5 | 24 | 5 | 4 | 18 |
| se:vi | 36562 | 64489 | 46 | 36 | 37 | 123 | 97 | 103 | 3 | 3 | 17 | 3 | 3 | 17 |
| se:solana | 17886 | 12596 | 35 | 69 | 14 | 122 | 87 | 115 | 1 | 1 | 18 | 1 | 1 | 18 |
| se:materials | 10957 | 20454 | 21 | 50 | 28 | 113 | 104 | 104 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:or | 11357 | 21772 | 19 | 52 | 28 | 112 | 94 | 97 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:sitecore | 32582 | 39873 | 20 | 52 | 24 | 105 | 86 | 91 | 1 | 1 | 3 | 1 | 1 | 3 |
| se:elementaryos | 18643 | 18325 | 30 | 54 | 14 | 101 | 90 | 87 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:iot | 5706 | 8326 | 24 | 39 | 28 | 97 | 94 | 81 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:poker | 7108 | 8817 | 37 | 32 | 17 | 89 | 84 | 77 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:civicrm | 37632 | 46264 | 16 | 40 | 17 | 83 | 65 | 80 | 1 | 0 | 0 | 0 | 0 | 0 |
| se:devops | 14029 | 11552 | 19 | 37 | 15 | 82 | 77 | 67 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:esperanto | 5479 | 5586 | 11 | 44 | 17 | 79 | 76 | 69 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:vegetarianism | 2478 | 3245 | 6 | 30 | 8 | 67 | 66 | 62 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:patents | 12873 | 13505 | 20 | 36 | 6 | 67 | 67 | 58 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:joomla | 19440 | 25604 | 7 | 38 | 10 | 65 | 50 | 65 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:monero | 10448 | 9465 | 12 | 31 | 5 | 64 | 41 | 58 | 3 | 2 | 8 | 2 | 2 | 8 |
| se:mythology | 5851 | 6353 | 18 | 25 | 17 | 63 | 60 | 58 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:opensource | 12840 | 26876 | 13 | 16 | 22 | 54 | 38 | 49 | 1 | 1 | 12 | 1 | 1 | 12 |
| se:langdev | 4335 | 11434 | 7 | 23 | 19 | 54 | 52 | 49 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:eosio | 5417 | 4401 | 7 | 29 | 7 | 52 | 37 | 45 | 2 | 2 | 8 | 2 | 2 | 8 |
| se:tor | 13225 | 13263 | 8 | 29 | 12 | 51 | 49 | 45 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:bioinformatics | 15201 | 21060 | 8 | 30 | 8 | 49 | 45 | 45 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:conlang | 2151 | 2931 | 6 | 23 | 8 | 42 | 39 | 40 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:opendata | 14288 | 12384 | 11 | 23 | 3 | 39 | 36 | 37 | 1 | 1 | 3 | 1 | 1 | 3 |
| se:proofassistants | 4232 | 7962 | 10 | 15 | 6 | 38 | 33 | 35 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:iota | 2476 | 2433 | 9 | 21 | 5 | 38 | 31 | 34 | 1 | 0 | 0 | 0 | 0 | 0 |
| se:beer | 3973 | 4023 | 7 | 18 | 9 | 36 | 32 | 31 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:bioacoustics | 1859 | 1700 | 4 | 15 | 0 | 24 | 23 | 24 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:craftcms | 32158 | 34649 | 7 | 13 | 3 | 23 | 23 | 20 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:ebooks | 4171 | 4033 | 3 | 13 | 3 | 22 | 22 | 21 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:freelancing | 7072 | 7600 | 1 | 13 | 6 | 21 | 12 | 14 | 1 | 1 | 4 | 1 | 1 | 4 |
| se:expatriates | 17021 | 31484 | 6 | 8 | 5 | 20 | 19 | 17 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:genai | 1341 | 1000 | 4 | 6 | 5 | 16 | 15 | 16 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:stackapps | 6013 | 11003 | 9 | 1 | 2 | 14 | 14 | 13 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:tridion | 18351 | 24196 | 4 | 7 | 2 | 14 | 12 | 12 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:moderators | 2044 | 1888 | 2 | 3 | 0 | 13 | 13 | 13 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:tezos | 4842 | 4312 | 2 | 8 | 2 | 12 | 10 | 12 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:expressionengine | 29213 | 31622 | 3 | 5 | 4 | 12 | 11 | 11 | 0 | 0 | 0 | 0 | 0 | 0 |
| se:stellar | 3278 | 1907 | 2 | 5 | 3 | 10 | 7 | 9 | 1 | 1 | 4 | 1 | 1 | 4 |

## Models, tokens and spend

| step | model | requests | input tokens | cache read | cache write | output tokens | US$ |
|---|---|---|---|---|---|---|---|
| stance labels | claude-haiku-5-5 (batch) | 41,386 (1 failed) | 47,389,018 | 0 | 0 | 3,378,358 | 3.21 |
| cards, pass 1 (non-SO + top 1,500 SO by authors) | claude-sonnet-5-5 (batch) | 3,833 (2 refused) | 7,507,281 | 14,577,060 | 26,670 | 1,265,760 | 15.33 |
| cards, pass 2 (remaining 2,044 SO + retries) | claude-sonnet-5-5 (batch) | 2,046 (4 refused) | 2,665,176 | 7,795,260 | 0 | 645,831 | 6.67 |
| card retry of the 4 refusals | claude-sonnet-5-5 (batch) | 4 (4 refused) | 7,935 | 3,810 | 11,430 | 0 | 0.02 |
| prompt tests (229-mention label batch, 6 + 2 synchronous calls) | haiku / sonnet | 237 | ~0.26M | | | ~17k | ~0.07 |
| **total** | | | | | | | **~US$25.3** of the US$45 budget |

Costs are computed from the usage the API returned, at batch prices (Haiku 5.5 $0.05 / $0.25 per MTok, Sonnet 5.5 $1 / $5, cache reads 10%, cache writes 125% of input). Budget guard: before the card batches the worst case (no cache hits) was US$44 for all 5,877 cards, which with labels would have passed US$45, so cards went out in two passes with Stack Overflow ranked by distinct-author count. Pass 1 showed cache hits cut the real cost to about US$0.004 per card, so the rest of Stack Overflow fitted and no SO video was capped in the end.

## Problems and caveats

- Stack Overflow dominates: 3,541 of 5,873 lines. Many SO videos are linked by questioners as "the tutorial I followed" (stance reference) rather than recommended; 906 video lines overall have 0 recommenders (their score is 0, so they sort last). The rule as published counts any non-joke, non-critical link, so they stay in.
- The `sample_url` flag caught few example-data links (152). Some URL-as-test-data videos still qualify on Stack Overflow, e.g. dQw4w9WgXcQ (regex questions about YouTube ids); it qualified in 6 communities (Stack Overflow, Puzzling, Code Golf, es.stackoverflow, Super User, UX), mostly as reference or joke.
- Community ids for the non-English Stack Overflows are `se:es.stackoverflow`, `se:pt.stackoverflow`, `se:ru.stackoverflow`, `se:ja.stackoverflow`. The merger's `community_label()` will title-case them badly ("Ru.Stackoverflow Stack Exchange"), and also sites such as `softwareengineering`, `3dprinting`, `gis`, `tex`, `unix`, `codereview`. The "why" sentences were rewritten with proper names (`NICE` and `NICE_FULL` in `scripts/06_cards.py`); adding the same names to `SE_NAMES` in `scripts/build_catalog.py` would make the UI labels match.
- Cooking is small: 1,738 mention rows but only 3 videos reach 3 authors in 3 months (links on most sites are scattered across many different videos).
- Question titles for answers and comments are joined from the same dump; posts are as of the dump date (edits after 2026-03-31 not reflected).
- The extraction counts differ from the live API figures given in the brief (for example Math 4,110 question bodies vs ~4.3k, Music 2,123 vs ~1.1k) because the API search matches differently and the dump excludes deleted posts; the SO figure (41,349) is after removing links in code.

## Rerun

`scripts/run.sh` lists the stages (setup, download, extract, candidates, label, collect-labels, cards-prep, cards, collect-cards, write). Start each rerun with an empty `work/` (labels are keyed by line number in `work/candidates.jsonl`). Point `01_download.py` at the newest community dump repo. Keys are read from stdin and never written to disk.
