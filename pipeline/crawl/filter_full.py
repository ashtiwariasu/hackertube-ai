"""A2 on a full crawl: youtube family excluded as source, heavy domains (>CAP pairs) count once,
Tranco top-100k as the reputable set, shared hosts never inherit rank. Writes agg/a2_*.tsv + a2_report.txt."""
import sys, collections, os, csv
d, cap = sys.argv[1], int(sys.argv[2])
YT = ("youtube.com", "youtu.be", "youtube-nocookie.com")
SHARED = {"blogspot.com", "sites.google.com", "wordpress.com", "tumblr.com", "wixsite.com", "weebly.com", "medium.com", "github.io", "substack.com", "livejournal.com"}
def is_yt(dom): return any(dom == y or dom.endswith("." + y) for y in YT)
tranco = {}
with open(os.path.expanduser("~/cc/tranco.csv")) as f:
    for rank, dom in csv.reader(f):
        r = int(rank)
        if r <= 100000 and dom not in SHARED: tranco[dom] = r
CC2 = {"co.uk","com.au","org.uk","co.jp","com.br","co.nz","ac.uk","gov.uk","co.za","com.mx","org.au","edu.au","ne.jp","or.jp","co.in","co.kr","com.tr","com.ar","gov.au","net.au","ac.jp","com.sg","com.hk","co.il","com.tw","edu.cn","com.cn","org.br"}
def reg(dom):  # registrable domain: last two labels, or three for co.uk-style suffixes
    p = dom.split(".")
    if len(p) >= 3 and ".".join(p[-2:]) in CC2: return ".".join(p[-3:])
    return ".".join(p[-2:]) if len(p) > 2 else dom
pairs_by_dom = collections.Counter()
with open(f"{d}/agg/pairs.tsv") as f:
    for l in f:
        v, dom = l.rstrip("\n").split("\t")
        if is_yt(dom): continue
        dom = reg(dom)
        pairs_by_dom[dom] += 1
heavy = {x for x, n in pairs_by_dom.items() if n > cap}
cat = {l.split("\t")[0].strip() for l in open(os.path.expanduser("~/cc/catalog_vids.txt"))}
light = collections.defaultdict(set); hv = set(); rep_light = collections.defaultdict(set); rep_hv = set(); rep_doms = collections.defaultdict(list)
with open(f"{d}/agg/pairs.tsv") as f:
    for l in f:
        v, dom = l.rstrip("\n").split("\t")
        if is_yt(dom): continue
        dom = reg(dom)
        if dom in heavy: hv.add(v)
        else: light[v].add(dom)
        r = tranco.get(dom)
        if r and dom not in SHARED:
            if dom in heavy: rep_hv.add(v)
            else: rep_light[v].add(dom)
            if dom not in rep_doms[v] and len(rep_doms[v]) < 8: rep_doms[v].append(dom)
def score(v, lc, hs): return len(lc.get(v, ())) + (1 if v in hs else 0)
allv = set(light) | hv
n3 = sum(1 for v in allv if score(v, light, hv) >= 3); c3 = sum(1 for v in cat if score(v, light, hv) >= 3)
repv = set(rep_light) | rep_hv
r3 = sum(1 for v in repv if score(v, rep_light, rep_hv) >= 3); rc3 = sum(1 for v in cat if score(v, rep_light, rep_hv) >= 3)
r2 = sum(1 for v in repv if score(v, rep_light, rep_hv) >= 2)
rep = [f"cap={cap} heavy_domains={len(heavy)} tranco_domains={len(tranco)}",
       f"all sources, count-once: videos>=3 {n3}  catalogue>=3 {c3} of {len(cat)}",
       f"tranco top-100k, count-once: videos>=2 {r2} videos>=3 {r3}  catalogue>=3 {rc3} of {len(cat)}"]
with open(f"{d}/agg/a2_tranco3.tsv", "w") as o:
    for v in repv:
        s = score(v, rep_light, rep_hv)
        if s >= 3: o.write(f"{v}\t{s}\t{','.join(rep_doms[v])}\n")
with open(f"{d}/agg/a2_tranco2.tsv", "w") as o:  # W6: the reputable 2+ rung (a superset of a2_tranco3)
    for v in repv:
        s = score(v, rep_light, rep_hv)
        if s >= 2: o.write(f"{v}\t{s}\t{','.join(rep_doms[v])}\n")
with open(f"{d}/agg/a2_all3.tsv", "w") as o:
    for v in allv:
        s = score(v, light, hv)
        if s >= 3: o.write(f"{v}\t{s}\n")
open(f"{d}/agg/a2_report.txt", "w").write("\n".join(rep) + "\n"); print("\n".join(rep))
