"""Backfill pool for the GPU: videos linked by 3+ sites (any sites, not only widely read ones) in BOTH the September and
October crawls, not judged yet, best first (most sites across the two months). Writes bf_pool.tsv as vid, count, domains."""
import json
SEP, OCT = "CC-MAIN-2026-39/agg", "CC-MAIN-2026-43/agg"
def counts(p):
    return {l.split("\t")[0]: int(l.split("\t")[1]) for l in open(p) if l.strip()}
a, b = counts(f"{SEP}/a2_all3.tsv"), counts(f"{OCT}/a2_all3.tsv")
judged = set()
for p in ("gate_full.jsonl", "bf_gate.jsonl"):
    try:
        judged |= {json.loads(l)["vid"] for l in open(p)}
    except FileNotFoundError:
        pass
pool = {v: a[v] + b[v] for v in a.keys() & b.keys() if v not in judged and len(v) == 11 and not v.startswith("-----")}
print("both months", len(a.keys() & b.keys()), "to judge", len(pool), flush=True)
doms = {v: set() for v in pool}
for p in (f"{SEP}/pairs.tsv", f"{OCT}/pairs.tsv"):
    with open(p) as f:
        for l in f:
            v, _, d = l.partition("\t")
            s = doms.get(v)
            if s is not None and len(s) < 12:
                s.add(d.strip())
with open("bf_pool.tsv", "w") as o:
    for v, n in sorted(pool.items(), key=lambda kv: -kv[1]):
        o.write(f"{v}\t{n}\t{','.join(sorted(doms[v]))}\n")
print("written", len(pool), flush=True)
