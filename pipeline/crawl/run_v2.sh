#!/bin/bash
# usage: run_v2.sh CRAWL WORKERS   (extract_v2: vid, domain, kind, pagetype, anchor text)
# A failed download must not count as done: pipefail, the output must be non-empty, back off on failure.
C=$1; W=${2:-16}
mkdir -p ~/cc/$C/out ~/cc/$C/log; cd ~/cc/$C
[ -s paths ] || curl -s https://data.commoncrawl.org/crawl-data/$C/wat.paths.gz | gunzip > paths
cp paths todo; date +%s > started; rm -f finished
cat todo | nice -n 10 xargs -P $W -I{} bash -c 'set -o pipefail; n=$(basename {} .warc.wat.gz); [ -s out/$n.tsv ] && exit 0; for try in 1 2 3 4; do if curl -sf --retry 2 --max-time 1200 https://data.commoncrawl.org/{} | timeout 1500 python3 -I ~/cc/extract_v2.py out/$n.tsv >> log/$n.log 2>&1 && [ -s out/$n.tsv ]; then exit 0; fi; rm -f out/$n.tsv; sleep $((30*try)); done; echo FAIL {} >> log/failed'
date +%s > finished
