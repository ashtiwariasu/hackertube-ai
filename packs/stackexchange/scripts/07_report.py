"""Write per-site stage counts and spend as a Markdown table fragment (REPORT.md is hand-finished around it).
Usage: python -I scripts/07_report.py work/ out_dir/ > work/report_tables.md
"""
import collections, glob, json, os, sys

work, outd = sys.argv[1], sys.argv[2]
stage = json.load(open(os.path.join(work, "stage_counts.json")))
qual = json.load(open(os.path.join(work, "qualify_counts.json")))
raw = {}
for f in glob.glob(os.path.join(work, "*", "stats.json")):
    s = json.load(open(f))
    raw[s["community"]] = s
final = collections.Counter(json.loads(l)["community"] for l in open(os.path.join(outd, "videos.jsonl")))
ment = collections.Counter(json.loads(l)["community"] for l in open(os.path.join(outd, "mentions.jsonl")))
cands = collections.Counter(json.loads(l)["community"] for l in open(os.path.join(work, "candidates.jsonl")))

cols = ["site", "posts", "comments", "q w/ YT", "a w/ YT", "c w/ YT", "mention rows", "distinct videos", ">=100 chars",
        "videos 3+ authors", "candidates (3 authors, 3 months)", "candidate mentions labelled", "qualified after stance",
        "final videos (YouTube ok + card)", "mentions written"]
print("| " + " | ".join(cols) + " |")
print("|" + "---|" * len(cols))
tot = collections.Counter()
rows = []
for c in sorted(set(stage) | set(raw), key=lambda c: -stage.get(c, {}).get("links", 0)):
    r, s, q = raw.get(c, {}), stage.get(c, {}), qual.get(c, {})
    vals = [r.get("posts_total", 0), r.get("comments_total", 0), r.get("posts_with_yt_q", 0), r.get("posts_with_yt_a", 0),
            r.get("comments_with_yt", 0), s.get("links", 0), s.get("videos", 0), s.get("counted_mentions", 0),
            s.get("videos_3_authors", 0), s.get("candidate_videos", 0), cands.get(c, 0), q.get("qualified_videos", 0),
            final.get(c, 0), ment.get(c, 0)]
    tot.update(dict(zip(cols[1:], vals)))
    rows.append((c, vals))
print("| **all sites** | " + " | ".join(str(tot[k]) for k in cols[1:]) + " |")
for c, vals in rows:
    print(f"| {c} | " + " | ".join(str(v) for v in vals) + " |")
dropped = collections.Counter()
for s in raw.values():
    for k in ("dropped_watermark", "dropped_community_user", "dropped_no_author"):
        dropped[k] += s.get(k, 0)
print()
print("Dropped before counting:", dict(dropped))
sp = json.load(open(os.path.join(work, "spend.json")))
print()
print("Spend:")
total = 0
for stage_name, runs in sp.items():
    for r in runs:
        total += r["usd"]
        print(f"- {stage_name}: {json.dumps({k: v for k, v in r.items() if k != 'batches'})}")
print(f"- total US$ {total:.2f}")
