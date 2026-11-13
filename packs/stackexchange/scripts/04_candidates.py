"""Apply the inclusion rule per site and write the candidate mentions to label.
Usage: python -I scripts/04_candidates.py work/ [so_cap]
Reads work/<host>/hits.jsonl. A mention counts when own_len >= 100 (author's own text, links and quoted
blocks stripped). A video is a candidate in a community when its counted mentions come from >= 3 distinct
authors in >= 3 distinct calendar months. Writes work/candidates.jsonl and work/stage_counts.json.
so_cap (optional): keep only the top-N Stack Overflow candidates by distinct-author count (budget guard).
"""
import collections, glob, json, os, sys

work = sys.argv[1]
so_cap = int(sys.argv[2]) if len(sys.argv) > 2 else 0
stage = collections.defaultdict(collections.Counter)
by_cv = collections.defaultdict(list)
for f in sorted(glob.glob(os.path.join(work, "*", "hits.jsonl"))):
    seen = set()
    for line in open(f, encoding="utf-8"):
        m = json.loads(line)
        k = (m["id"], m["vid"])
        if k in seen:
            continue
        seen.add(k)
        c = m["community"]
        stage[c]["links"] += 1
        stage[c]["_vids"] += 0
        by_cv[(c, m["vid"])].append(m)

for (c, vid), ms in by_cv.items():
    stage[c]["videos"] += 1
    counted = [m for m in ms if m["own_len"] >= 100]
    stage[c]["counted_mentions"] += len(counted)
    authors = {m["author_key"] for m in counted}
    months = {m["month"] for m in counted}
    if len(authors) >= 3:
        stage[c]["videos_3_authors"] += 1
    if len(authors) >= 3 and len(months) >= 3:
        stage[c]["candidate_videos"] += 1
        stage[c]["candidate_mentions"] += len(counted)

cands = []
for (c, vid), ms in by_cv.items():
    counted = [m for m in ms if m["own_len"] >= 100]
    authors = {m["author_key"] for m in counted}
    months = {m["month"] for m in counted}
    if len(authors) >= 3 and len(months) >= 3:
        cands.append((c, vid, len(authors), counted))

if so_cap:
    so = sorted([x for x in cands if x[0] == "se:stackoverflow"], key=lambda x: -x[2])
    drop = {(x[0], x[1]) for x in so[so_cap:]}
    cands = [x for x in cands if (x[0], x[1]) not in drop]
    stage["se:stackoverflow"]["so_cap_dropped_videos"] = len(drop)

with open(os.path.join(work, "candidates.jsonl"), "w", encoding="utf-8") as out:
    for c, vid, na, counted in cands:
        for m in counted:
            out.write(json.dumps(m, ensure_ascii=False) + "\n")
for c in stage:
    stage[c].pop("_vids", None)
json.dump({c: dict(v) for c, v in sorted(stage.items())}, open(os.path.join(work, "stage_counts.json"), "w"), indent=1)
tot = collections.Counter()
for v in stage.values():
    tot.update(v)
print("communities", len(stage), dict(tot))
for c, v in sorted(stage.items(), key=lambda kv: -kv[1]["links"])[:25]:
    print(c, dict(v))
