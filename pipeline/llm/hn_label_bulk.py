"""H3: label every HN comment+video not already labelled in the dataset. Resumable; appends to hn_labels.jsonl."""
import json, sys, time, os, threading
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0, "/home/ubuntu/llm"); from label_lib import label
OUT = "/home/ubuntu/hn/hn_labels.jsonl"
known = {int(x) for x in open("/home/ubuntu/llm/known_ids.txt").read().split()}
done = set()
if os.path.exists(OUT):
    for l in open(OUT):
        try: d = json.loads(l); done.add((d["id"], d["vid"]))
        except ValueError: pass
todo = []
for l in open("/home/ubuntu/hn/hn_yt.jsonl"):
    r = json.loads(l)
    if r["id"] in known: continue
    for v in r["vids"]:
        if (r["id"], v) not in done: todo.append((r, v))
print(f"known {len(known)} done {len(done)} todo {len(todo)}", flush=True)
lock = threading.Lock(); out = open(OUT, "a"); n = 0; t0 = time.time()
def one(item):
    global n
    r, v = item
    text = r["text"] if len(r["vids"]) == 1 else r["text"] + f"\n(The link in question is https://youtu.be/{v})"
    for attempt in range(3):
        try: d, u = label(r.get("story_title"), text); break
        except Exception as e:  # noqa: BLE001
            d = {"stance": "ERR", "why": str(e)[:100], "quote": ""}; time.sleep(2)
    row = {"id": r["id"], "vid": v, "author": r["author"], "month": r["month"], "story_title": r.get("story_title"), "source": "qwen3-32b", **d}
    with lock:
        out.write(json.dumps(row) + "\n"); n += 1
        if n % 2000 == 0:
            out.flush(); print(f"{n}/{len(todo)} {n/(time.time()-t0):.1f}/s", flush=True)
with ThreadPoolExecutor(96) as ex: list(ex.map(one, todo))
out.close(); print(f"DONE {n} in {time.time()-t0:.0f}s", flush=True)
