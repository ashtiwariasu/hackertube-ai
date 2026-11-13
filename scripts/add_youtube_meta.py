"""Merge YouTube's own metadata (fetched with your own API key by scripts/fetch_youtube_meta.py) into the catalogue:
duration, view count, publish date and channel id. Videos YouTube no longer returns (deleted or private) are dropped,
along with their rows in the embedding matrices.

    .venv/bin/python -I scripts/add_youtube_meta.py youtube_meta.jsonl [--data app/data]

YouTube's API policies ask you to refresh stored metadata at least every 30 days; rerun both scripts to do that.
"""
import argparse, json, re

import numpy as np

ISO = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def seconds(iso):
    m = ISO.match(iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("meta")
    ap.add_argument("--data", default="app/data")
    a = ap.parse_args()
    items = {}
    for line in open(a.meta, encoding="utf-8"):
        r = json.loads(line)
        items[r["vid"]] = r.get("item")
    videos = json.load(open(f"{a.data}/videos.json", encoding="utf-8"))
    keep, gone = [], 0
    for i, v in enumerate(videos):
        it = items.get(v["vid"], "missing")
        if it is None:
            gone += 1
            continue
        if isinstance(it, dict):
            sn, cd, st = it.get("snippet") or {}, it.get("contentDetails") or {}, it.get("statistics") or {}
            v.update(dur=seconds(cd.get("duration")), views=int(st.get("viewCount") or 0),
                     published=(sn.get("publishedAt") or "")[:10], channel_id=sn.get("channelId") or "")
        keep.append(i)
    json.dump([videos[i] for i in keep], open(f"{a.data}/videos.json", "w", encoding="utf-8"), ensure_ascii=False)
    for name in ("embeddings.npy", "title_emb.npy"):
        m = np.load(f"{a.data}/{name}")
        np.save(f"{a.data}/{name}", m[keep])
    print(f"updated {sum(1 for i in keep if isinstance(items.get(videos[i]['vid']), dict))} videos, "
          f"dropped {gone} that YouTube no longer returns, {len(keep)} left")


if __name__ == "__main__":
    main()
