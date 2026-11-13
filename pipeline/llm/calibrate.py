"""C2 calibration: label the 300 gold comments and compare with the dataset's Claude labels."""
import json, sys, time, collections
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, "/home/ubuntu/llm"); from label_lib import label
gold = json.load(open("/home/ubuntu/llm/gold300.json"))
REC = {"strong_rec", "rec"}
def one(r):
    try:
        d, u = label(r.get("story_title"), r.get("text")); return r, d, u
    except Exception as e:  # noqa: BLE001
        return r, {"stance": "ERR", "why": str(e)[:80], "quote": ""}, {}
t0 = time.time()
with ThreadPoolExecutor(96) as ex: res = list(ex.map(one, gold))
dt = time.time() - t0
exact = sum(1 for r, d, _ in res if d["stance"] == r["stance"])
binary = sum(1 for r, d, _ in res if (d["stance"] in REC) == (r["stance"] in REC))
errs = sum(1 for _, d, _ in res if d["stance"] == "ERR")
conf = collections.Counter((r["stance"], d["stance"]) for r, d, _ in res)
toks = sum(u.get("total_tokens", 0) for _, _, u in res)
print(f"n={len(res)} errors={errs} exact={exact/len(res)*100:.1f}% rec-vs-not={binary/len(res)*100:.1f}% wall={dt:.0f}s rate={len(res)/dt:.1f}/s tokens={toks}")
print("confusion (gold -> model):"); [print(f"  {g:10s} -> {m:10s} {n}") for (g, m), n in sorted(conf.items(), key=lambda x: -x[1])[:15]]
json.dump([{"gold": r, "model": d} for r, d, _ in res], open("/home/ubuntu/llm/calibration.json", "w"), indent=1)
print("examples:"); [print(f"  gold={r['stance']} model={d['stance']} | {d['why'][:110]}") for r, d, _ in res[:8]]
