"""W3: merge Qwen's topic picks (web_topics.jsonl) into a web pack. Usage: python3 -I add_topics.py PACK.jsonl TOPICS.jsonl OUT.jsonl"""
import json, sys
pack_p, topics_p, out_p = sys.argv[1:4]
tp = {}
for line in open(topics_p):
    r = json.loads(line)
    if r.get("topics"):
        tp[r["vid"]] = r["topics"]
n = m = 0
with open(out_p, "w") as o:
    for line in open(pack_p):
        if not line.strip():
            continue
        r = json.loads(line); n += 1
        if r["vid"] in tp:
            r["topics"] = tp[r["vid"]]; m += 1
        o.write(json.dumps(r) + "\n")
print("rows", n, "with topics", m)
