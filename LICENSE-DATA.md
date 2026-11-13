# Data licence

The HackerTube AI data release (the `hackertube-format` and `app-data` folders, and the demo catalogue in `app/data`)
is made available under the [Open Data Commons Attribution License (ODC-By) v1.0](https://opendatacommons.org/licenses/by/1-0/),
the same licence as the HackerTube dataset it extends. Please credit **HackerTube AI** (https://hackertube.ai) and
**HackerTube** (https://hackertube.org).

Parts of it come from other sources and keep their own terms:

- **Hacker News**: comments and video cards from HackerTube's dataset (ODC-By 1.0), derived from the
  [open-index/hacker-news](https://huggingface.co/datasets/open-index/hacker-news) archive. Hacker News data is
  provided by Y Combinator; comments remain the work of their authors.
- **Stack Exchange**: quoted posts and comments (`mentions_other.jsonl` rows marked `"license": "CC BY-SA 4.0"`, and
  Stack Exchange mentions in `app-data/mentions.json`) are user contributions licensed under
  [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). Each row links to the original post and names its
  author; keep both if you republish them, and share adaptations of that text under the same licence.
- **The open web**: site counts and linking domains are derived from [Common Crawl](https://commoncrawl.org), used
  under its [terms of use](https://commoncrawl.org/terms-of-use).
- **Wikipedia**: counts of Wikipedia articles citing a video, and those articles' titles.
- **YouTube**: video titles and channel names belong to their creators. No other YouTube metadata is included.

Summaries, "why" lines, stance labels and topics were written by language models (Claude and Qwen) and are part of the
release under ODC-By. Not affiliated with YouTube, Google, Hacker News, Y Combinator, Stack Exchange or Common Crawl.
