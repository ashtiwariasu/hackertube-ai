#!/bin/bash
# Keeps the H100 busy between crawls: judges the backfill pool (bf_pool.tsv) in batches with its own files
# (bf_meta, bf_gate, bf_topics), so it never writes the files the crawl chain appends to. YouTube's daily quota is
# the limit: at most BF_DAY ids of metadata a day, leaving room for the crawl chain. Stops when the pool is done.
cd ~/cc
BATCH=25000; BF_DAY=${BF_DAY:-250000}
log(){ echo "$(date -u) $*" >> bf_loop.log; }
[ -s bf_pool.tsv ] || { log "building pool"; python3 -I bf_prep.py >> bf_loop.log 2>&1; }
export YOUTUBE_API_KEY="$(cat ~/.yt_key)"
touch bf_meta.jsonl bf_gate.jsonl bf_topics.jsonl
while true; do
  day=$(TZ=America/Los_Angeles date +%F)   # YouTube quota resets at midnight Pacific
  used=$(cat bf_quota.$day 2>/dev/null || echo 0)
  if [ "$used" -ge "$BF_DAY" ]; then sleep 600; continue; fi
  python3 -I - <<PY
import json
done = {json.loads(l)["id"] for l in open("bf_meta.jsonl")}
n = 0
with open("bf_pool.tsv") as f, open("bf_batch.tsv", "w") as o:
    for l in f:
        if l.split("\t")[0] not in done:
            o.write(l); n += 1
            if n >= $BATCH: break
print(n)
PY
  [ -s bf_batch.tsv ] || { log "pool done"; break; }
  python3 -I fetch_meta_ids.py bf_batch.tsv bf_meta.jsonl > bf_fetch.log 2>&1
  echo $((used + $(wc -l < bf_batch.tsv))) > bf_quota.$day
  if grep -q "GIVING UP" bf_fetch.log; then log "fetch stopped (quota?) $(tail -1 bf_fetch.log)"; sleep 3600; continue; fi
  python3 -I web_gate.py bf_meta.jsonl bf_batch.tsv bf_gate.jsonl --workers 64 > bf_gate.log 2>&1
  python3 -I web_topics.py bf_meta.jsonl bf_gate.jsonl taxonomy.json bf_topics.jsonl --workers 64 > bf_topics.log 2>&1
  log "batch done meta=$(wc -l < bf_meta.jsonl) gate=$(wc -l < bf_gate.jsonl) kept=$(grep -c '"keep": true' bf_gate.jsonl) topics=$(wc -l < bf_topics.jsonl)"
done
