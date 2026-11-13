"""W3: pick 1-2 taxonomy topics for each gate-kept video, from the topics of the field the gate chose.
Usage: python3 -I web_topics.py META.jsonl GATE.jsonl TAXONOMY.json OUT.jsonl [--workers 96]   (resumable)"""
import json, os, queue, sys, threading, time, urllib.request
URL = "http://127.0.0.1:8100/v1/chat/completions"
SYSTEM = """You file YouTube videos into a library's topic shelves. You get one video and the shelves of the field it belongs to.
Pick the one shelf where a reader browsing that topic would most expect to find this video. Add a second shelf only when the video is just as much about it.
Return JSON: topics (one or two shelf ids from the list, best first)."""
USER = """Field: {area}
Shelves:
{shelves}

Video
Title: {title}
Channel: {channel}
What it is: {why}
Description: {desc}"""

def main():
    meta_p, gate_p, tax_p, out_p = sys.argv[1:5]
    workers = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 96
    tax = json.load(open(tax_p))
    areas = {a["id"]: a for a in tax["areas"]}
    meta = {}
    for line in open(meta_p):
        r = json.loads(line)
        if r.get("title"): meta[r["id"]] = r
    done = set()
    if os.path.exists(out_p):
        for line in open(out_p):
            try: done.add(json.loads(line)["vid"])
            except Exception: pass
    rows = []
    for line in open(gate_p):
        g = json.loads(line)
        if g.get("keep") and g.get("english") and g.get("area") in areas and g["vid"] in meta and g["vid"] not in done:
            rows.append(g)
    print("todo", len(rows), "done", len(done), flush=True)
    q = queue.Queue(); [q.put(r) for r in rows]
    lock = threading.Lock(); out = open(out_p, "a"); n = [0]; t0 = time.time()
    def call(g):
        a = areas[g["area"]]; ids = [c["id"] for c in a["categories"]]
        shelves = "\n".join(f"- {c['id']}: {c['name']}. {c.get('description','')[:220]}" for c in a["categories"])
        m = meta[g["vid"]]
        schema = {"type": "object", "properties": {"topics": {"type": "array", "items": {"type": "string", "enum": ids}, "minItems": 1, "maxItems": 2}},
                  "required": ["topics"], "additionalProperties": False}
        body = {"model": "labeller", "messages": [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": USER.format(area=a["name"], shelves=shelves, title=m.get("title", ""), channel=m.get("channel", ""),
                                                         why=g.get("why", ""), desc=(m.get("desc") or "")[:500].replace("\n", " "))}],
                "max_tokens": 80, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False},
                "response_format": {"type": "json_schema", "json_schema": {"name": "topics", "schema": schema}}}
        req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        err = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=300) as r:
                    t = json.loads(json.load(r)["choices"][0]["message"]["content"])["topics"]
                t = [x for x in dict.fromkeys(t) if x in ids][:2]
                if t: return {"topics": t}
                err = "empty"
            except Exception as e:  # noqa: BLE001
                err = str(e)
            time.sleep(2 * (attempt + 1))
        return {"error": err}
    def work():
        while True:
            try: g = q.get_nowait()
            except queue.Empty: return
            res = call(g)
            with lock:
                out.write(json.dumps({"vid": g["vid"], "area": g["area"], **res}) + "\n"); out.flush(); n[0] += 1
                if n[0] % 1000 == 0: print(n[0], f"{n[0]/(time.time()-t0):.1f}/s", flush=True)
    ts = [threading.Thread(target=work) for _ in range(workers)]; [t.start() for t in ts]; [t.join() for t in ts]
    print("done", n[0], f"{time.time()-t0:.0f}s", flush=True)

if __name__ == "__main__":
    main()
