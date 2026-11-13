"""Fetch YouTube metadata for every catalogue video. Standard library only; resumable (already-fetched ids are skipped).
Usage: YOUTUBE_API_KEY=... python3 -I scripts/fetch_youtube_meta.py VIDEOS youtube_meta.jsonl
VIDEOS is a videos.jsonl (one {"vid": ...} per line, e.g. a HackerTube download) or this app's app/data/videos.json.
Then merge it into the app's catalogue with scripts/add_youtube_meta.py.
"""
import json, os, sys, time, urllib.parse, urllib.request

key = os.environ["YOUTUBE_API_KEY"]
src, out = sys.argv[1], sys.argv[2]
if src.endswith(".json"):
    vids = [v["vid"] for v in json.load(open(src, encoding="utf-8"))]
else:
    vids = [json.loads(line)["vid"] for line in open(src, encoding="utf-8")]
done = set()
if os.path.exists(out):
    done = {json.loads(line)["vid"] for line in open(out, encoding="utf-8")}
todo = [v for v in vids if v not in done]
print("total", len(vids), "todo", len(todo), flush=True)
with open(out, "a", encoding="utf-8") as f:
    for i in range(0, len(todo), 50):
        ids = todo[i:i + 50]
        q = urllib.parse.urlencode({"part": "snippet,contentDetails,statistics,status",
                                    "id": ",".join(ids), "key": key})
        for attempt in range(5):
            try:
                with urllib.request.urlopen("https://www.googleapis.com/youtube/v3/videos?" + q, timeout=60) as r:
                    items = json.load(r)["items"]
                break
            except Exception as e:
                print("retry", attempt, e, flush=True)
                time.sleep(2 * (attempt + 1))
        else:
            raise SystemExit("gave up")
        got = {it["id"]: it for it in items}
        for v in ids:
            f.write(json.dumps({"vid": v, "item": got.get(v)}) + "\n")
        f.flush()
        if (i // 50) % 20 == 0:
            print("fetched", i + len(ids), flush=True)
        time.sleep(0.15)
print("done", flush=True)
