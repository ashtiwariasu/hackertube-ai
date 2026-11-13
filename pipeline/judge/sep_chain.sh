#!/bin/bash
# September crawl (CC-MAIN-2026-39): aggregate, reputable filter, then metadata, Qwen gate and topics for
# the videos not judged before. Every step resumes; the chain can be re-run from the top.
cd ~/cc
C=CC-MAIN-2026-39
log(){ echo "$(date -u) $*" >> sep_chain.log; }
log start
[ -s $C/agg/vid_domains.tsv ] || bash aggregate_box.sh $C > sep_agg.log 2>&1
log "aggregate done $(tr "\n" " " < $C/agg/summary.txt)"
[ -s $C/agg/a2_tranco2.tsv ] || python3 -I filter_full.py ~/cc/$C 3000 > sep_filter.log 2>&1
log "filter done tranco2=$(wc -l < $C/agg/a2_tranco2.tsv) tranco3=$(wc -l < $C/agg/a2_tranco3.tsv)"
python3 - <<PY
import json
seen = {json.loads(l)["vid"] for l in open("gate_full.jsonl")}
n = new = 0
with open("$C/agg/a2_tranco2.tsv") as f, open("sep_new.tsv", "w") as o:
    for l in f:
        n += 1
        if l.split("\t")[0] not in seen:
            o.write(l); new += 1
print(n, new)
open("sep_new.count", "w").write(f"{n} {new}\n")
PY
log "new candidates $(cat sep_new.count)"
export YOUTUBE_API_KEY="$(cat ~/.yt_key)"
python3 -I fetch_meta_ids.py sep_new.tsv a2_meta.jsonl > sep_fetch.log 2>&1
log "fetch done meta=$(wc -l < a2_meta.jsonl) $(tail -1 sep_fetch.log)"
python3 -I web_gate.py a2_meta.jsonl sep_new.tsv gate_full.jsonl --workers 96 > sep_gate.log 2>&1
log "gate done rows=$(wc -l < gate_full.jsonl)"
python3 -I web_topics.py a2_meta.jsonl gate_full.jsonl taxonomy.json web_topics.jsonl --workers 96 > sep_topics.log 2>&1
log "topics done rows=$(wc -l < web_topics.jsonl)"
