#!/bin/bash
# Judge one crawl once its extraction is done: aggregate, reputable filter, then metadata, Qwen gate and
# topics for videos not judged before. Same steps as sep_chain.sh; every step resumes. Usage: judge_chain.sh CRAWL TAG
cd ~/cc
C=$1; T=$2
log(){ echo "$(date -u) $C $*" >> judge_chain.log; }
log "waiting for extraction"
until grep -q "done $C " chain.log; do sleep 300; done
log start
[ -s $C/agg/vid_domains.tsv ] || bash aggregate_box.sh $C > ${T}_agg.log 2>&1
log "aggregate done $(tr "\n" " " < $C/agg/summary.txt)"
[ -s $C/agg/a2_tranco2.tsv ] || python3 -I filter_full.py ~/cc/$C 3000 > ${T}_filter.log 2>&1
log "filter done tranco2=$(wc -l < $C/agg/a2_tranco2.tsv) tranco3=$(wc -l < $C/agg/a2_tranco3.tsv)"
python3 - <<PY
import json
seen = {json.loads(l)["vid"] for l in open("gate_full.jsonl")}
n = new = 0
with open("$C/agg/a2_tranco2.tsv") as f, open("${T}_new.tsv", "w") as o:
    for l in f:
        n += 1
        if l.split("\t")[0] not in seen:
            o.write(l); new += 1
open("${T}_new.count", "w").write(f"{n} {new}\n")
PY
log "new candidates $(cat ${T}_new.count)"
cp -n gate_full.jsonl gate_full.jsonl.bak-before-$T; cp -n web_topics.jsonl web_topics.jsonl.bak-before-$T; cp -n a2_meta.jsonl a2_meta.jsonl.bak-before-$T
export YOUTUBE_API_KEY="$(cat ~/.yt_key)"
python3 -I fetch_meta_ids.py ${T}_new.tsv a2_meta.jsonl > ${T}_fetch.log 2>&1
log "fetch done meta=$(wc -l < a2_meta.jsonl) $(tail -1 ${T}_fetch.log)"
python3 -I web_gate.py a2_meta.jsonl ${T}_new.tsv gate_full.jsonl --workers 96 > ${T}_gate.log 2>&1
log "gate done rows=$(wc -l < gate_full.jsonl)"
python3 -I web_topics.py a2_meta.jsonl gate_full.jsonl taxonomy.json web_topics.jsonl --workers 96 > ${T}_topics.log 2>&1
log "topics done rows=$(wc -l < web_topics.jsonl)"
