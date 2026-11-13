"""Build the small demo catalogue that ships in app/data, so a fresh clone runs (and its tests pass) without
downloading anything. It's a slice of the full data release: the best-scored videos from each source, with YouTube's
own metadata left out.

    .venv/bin/python -I scripts/build_demo.py SRC_APP_DATA [--out app/data] [--hn 1000] [--se 400] [--web 400]

SRC_APP_DATA is an app-data folder: the `app-data` folder of a data release, or a full app/data.
"""
import argparse, json, os, shutil

import numpy as np

YOUTUBE_ONLY = ("dur", "views", "published", "channel_id")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--out", default="app/data")
    ap.add_argument("--hn", type=int, default=1000)
    ap.add_argument("--se", type=int, default=400)
    ap.add_argument("--web", type=int, default=400)
    ap.add_argument("--mentions-per-video", type=int, default=5)
    a = ap.parse_args()
    if os.path.abspath(a.src) == os.path.abspath(a.out):
        raise SystemExit("source and output are the same folder")

    videos = json.load(open(f"{a.src}/videos.json", encoding="utf-8"))
    emb, title_emb = np.load(f"{a.src}/embeddings.npy"), np.load(f"{a.src}/title_emb.npy")

    def source(v):
        p = str(v.get("primary") or "")
        return "web" if p == "web" else "se" if p.startswith("se:") else "hn" if p == "hn" else "other"

    learn = {"talk", "lecture", "explainer", "documentary", "tutorial", "interview", "demo"}
    keep = set()
    for kind, n in (("hn", a.hn), ("se", a.se), ("web", a.web)):
        pool = [v for v in videos if source(v) == kind]
        if kind == "web":   # the open web's most-linked videos are mostly music; the demo shows what it's for
            pool = [v for v in pool if (v.get("web") or {}).get("kind") in learn]
        best = sorted(pool, key=lambda v: -float(v.get("score") or 0))
        keep.update(v["vid"] for v in best[:n])
    rows = [i for i, v in enumerate(videos) if v["vid"] in keep]
    demo = [{k: x for k, x in videos[i].items() if k not in YOUTUBE_ONLY} for i in rows]

    os.makedirs(a.out, exist_ok=True)
    json.dump(demo, open(f"{a.out}/videos.json", "w", encoding="utf-8"), ensure_ascii=False)
    np.save(f"{a.out}/embeddings.npy", emb[rows].astype(np.float16))
    np.save(f"{a.out}/title_emb.npy", title_emb[rows].astype(np.float16))

    mentions = json.load(open(f"{a.src}/mentions.json", encoding="utf-8"))
    json.dump({vid: ms[:a.mentions_per_video] for vid, ms in mentions.items() if vid in keep},
              open(f"{a.out}/mentions.json", "w", encoding="utf-8"), ensure_ascii=False)
    related = json.load(open(f"{a.src}/related.json", encoding="utf-8"))
    json.dump({vid: [r for r in rs if r in keep] for vid, rs in related.items() if vid in keep},
              open(f"{a.out}/related.json", "w", encoding="utf-8"))
    authors = json.load(open(f"{a.src}/authors.json", encoding="utf-8"))
    json.dump({name: [x for x in vids if x in keep] for name, vids in authors.items() if any(x in keep for x in vids)},
              open(f"{a.out}/authors.json", "w", encoding="utf-8"), ensure_ascii=False)
    communities = json.load(open(f"{a.src}/communities.json", encoding="utf-8"))
    counts = {}
    for v in demo:
        for c in (v.get("comm") or {}):
            counts[c] = counts.get(c, 0) + 1
        if v.get("web"):
            counts["web"] = counts.get("web", 0) + 1
    json.dump({c: dict(meta, videos=counts[c]) for c, meta in communities.items() if counts.get(c)},
              open(f"{a.out}/communities.json", "w", encoding="utf-8"), ensure_ascii=False)
    for name in ("taxonomy.json", "topic_intros.json"):
        shutil.copy(f"{a.src}/{name}", f"{a.out}/{name}")
    by = {}
    for v in demo:
        by[source(v)] = by.get(source(v), 0) + 1
    print(f"demo catalogue: {len(demo)} videos {by} -> {a.out}")


if __name__ == "__main__":
    main()
