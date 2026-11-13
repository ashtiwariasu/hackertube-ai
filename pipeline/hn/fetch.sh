set -e
cd ~/hn
curl -s "https://huggingface.co/api/datasets/open-index/hacker-news?blobs=true" | python3 -c "import sys,json; [print(x[\"rfilename\"]) for x in json.load(sys.stdin)[\"siblings\"] if x[\"rfilename\"].endswith(\".parquet\")]" > files.txt
wc -l files.txt
cat files.txt | xargs -P 8 -I{} bash -c "mkdir -p parquet/\$(dirname {}); [ -s parquet/{} ] || curl -sfL --retry 3 -o parquet/{} https://huggingface.co/datasets/open-index/hacker-news/resolve/main/{} || echo FAIL {} >> fetch.failed"
du -sh parquet; echo FETCH_DONE
python3 -m venv venv && . venv/bin/activate && pip install -q duckdb pyarrow && echo VENV_DONE
