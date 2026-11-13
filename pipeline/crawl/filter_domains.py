"""A2: drop YouTube itself as a source, cap embed farms, report what the filter changes.
usage: filter_domains.py CRAWL_DIR CAP  -> agg/vid_domains_filtered.tsv, agg/filter_report.txt, agg/excluded_domains.txt"""
import sys, collections, os
d = sys.argv[1]; cap = int(sys.argv[2])
YT = ("youtube.com", "youtu.be", "youtube-nocookie.com", "m.youtube.com", "music.youtube.com", "googlevideo.com", "ytimg.com")
pairs_by_dom = collections.Counter()
with open(f"{d}/agg/pairs.tsv") as f:
    for l in f: pairs_by_dom[l.rstrip("\n").split("\t")[1]] += 1
excluded = {x for x in pairs_by_dom if x in YT or x.endswith(".youtube.com")}
farms = {x for x, n in pairs_by_dom.items() if n > cap and x not in excluded}
cat = {l.split("\t")[0] for l in open(os.path.expanduser("~/cc/catalog_vids.txt"))}
vd = collections.Counter(); vd_all = collections.Counter()
with open(f"{d}/agg/pairs.tsv") as f:
    for l in f:
        v, dom = l.rstrip("\n").split("\t"); vd_all[v] += 1
        if dom not in excluded and dom not in farms: vd[v] += 1
with open(f"{d}/agg/vid_domains_filtered.tsv", "w") as o:
    for v, n in vd.items(): o.write(f"{v}\t{n}\n")
with open(f"{d}/agg/excluded_domains.txt", "w") as o:
    for x in sorted(excluded): o.write(f"{x}\tyoutube\t{pairs_by_dom[x]}\n")
    for x in sorted(farms, key=lambda x: -pairs_by_dom[x]): o.write(f"{x}\tfarm\t{pairs_by_dom[x]}\n")
def tiers(c): return {k: sum(1 for n in c.values() if n >= k) for k in (2, 3, 5, 10)}
b, a = tiers(vd_all), tiers(vd)
cat_b = sum(1 for v in cat if vd_all.get(v, 0) >= 3); cat_a = sum(1 for v in cat if vd.get(v, 0) >= 3)
rep = [f"cap={cap} excluded_youtube={len(excluded)} farms={len(farms)} farm_pairs={sum(pairs_by_dom[x] for x in farms)} of {sum(pairs_by_dom.values())}",
       f"videos by tier before: {b}", f"videos by tier after:  {a}",
       f"catalogue videos with >=3 domains before {cat_b} after {cat_a} ({(cat_a/cat_b*100 if cat_b else 0):.1f}%)"]
open(f"{d}/agg/filter_report.txt", "w").write("\n".join(rep) + "\n"); print("\n".join(rep))
