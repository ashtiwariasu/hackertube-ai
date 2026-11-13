#!/usr/bin/env bash
# Quarterly rerun of the Stack Exchange pack, on a GPU box under ~/packs/stackexchange/.
# Copy this scripts/ folder to the box first, plus app/data/taxonomy.json as scripts/taxonomy.json.
# Keys are read from stdin (never written to disk), e.g. from your own machine:
#   (gcloud secrets versions access latest --secret ANTHROPIC_API_KEY --project $PROJECT; echo
#    gcloud secrets versions access latest --secret hackertube-youtube-api-key --project $PROJECT; echo) |
#   ssh ... 'cd ~/packs/stackexchange && bash scripts/run.sh STAGE'
# Run stage by stage (each long stage is started with nohup and polled; keep SSH sessions short):
#   setup | download | extract | candidates | label | collect-labels | cards-prep | cards | collect-cards | write
set -euo pipefail
cd ~/packs/stackexchange
read -r ANTHROPIC_API_KEY || true; read -r YOUTUBE_API_KEY || true
export ANTHROPIC_API_KEY YOUTUBE_API_KEY
PY=".venv/bin/python -I"; T=scripts/taxonomy.json
case "$1" in
  setup)      mkdir -p raw work logs out; python3 -m venv .venv; .venv/bin/pip install -q huggingface_hub anthropic
              command -v 7zz || sudo apt-get install -y 7zip ;;
  # point 01_download.py at the newest community dump repo (second argument) when one is published
  download)   setsid nohup $PY scripts/01_download.py raw/ ${2:-flymin/stackexchange_20260331} > logs/download.log 2>&1 < /dev/null & ;;
  extract)    setsid nohup $PY scripts/03_extract_all.py raw/ work/ logs/download.log 12 > logs/extract.log 2>&1 < /dev/null & ;;
  candidates) $PY scripts/04_candidates.py work/ ;;
  label)      $PY scripts/05_label.py estimate work/ && $PY scripts/05_label.py submit work/ ;;
  collect-labels) setsid nohup $PY scripts/05_label.py collect work/ > logs/label.log 2>&1 < /dev/null & ;;
  cards-prep) $PY scripts/06_cards.py qualify work/ $T && $PY scripts/06_cards.py ytmeta work/ $T && $PY scripts/06_cards.py estimate work/ $T ;;
  # cards: all non-SO videos plus the Stack Overflow slice [from, to) ranked by distinct authors (budget guard)
  cards)      $PY scripts/06_cards.py submit work/ $T ${2:-0} ${3:-100000} ;;
  collect-cards) setsid nohup $PY scripts/06_cards.py collect work/ $T > logs/cards.log 2>&1 < /dev/null & ;;
  write)      $PY scripts/06_cards.py write work/ $T out/ && $PY scripts/07_report.py work/ out/ > work/report_tables.md ;;
  *) echo "unknown stage"; exit 1 ;;
esac
