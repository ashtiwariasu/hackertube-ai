"""Download the Stack Exchange dump (HF mirror) for all non-meta sites.
Usage (on the box): HF_HUB_ENABLE_HF_TRANSFER=1 .venv/bin/python scripts/01_download.py raw/ [repo_id]
Skips meta sites. Smallest archives first so per-site extraction can start early.
"""
import os, sys, time
from huggingface_hub import HfApi, hf_hub_download

out = sys.argv[1]
repo = sys.argv[2] if len(sys.argv) > 2 else "flymin/stackexchange_20260331"
api = HfApi()
files = [f for f in api.list_repo_tree(repo, repo_type="dataset", recursive=True)
         if getattr(f, "size", None) and f.path.endswith(".7z")]
files = [f for f in files if ".meta." not in f.path and not f.path.startswith("meta.")]
files.sort(key=lambda f: f.size)
print("sites", len(files), "GB", round(sum(f.size for f in files) / 1e9, 1), flush=True)
os.makedirs(out, exist_ok=True)
for f in files:
    dest = os.path.join(out, f.path)
    if os.path.exists(dest + ".done") or os.path.exists(dest + ".extracted"):
        continue
    t = time.time()
    for attempt in range(5):
        try:
            hf_hub_download(repo, f.path, repo_type="dataset", local_dir=out)
            break
        except Exception as e:
            print("retry", f.path, attempt, e, flush=True)
            time.sleep(10 * (attempt + 1))
    else:
        print("FAILED", f.path, flush=True)
        continue
    open(dest + ".done", "w").close()
    print("got", f.path, round(f.size / 1e6), "MB", round(time.time() - t), "s", flush=True)
print("ALL DONE", flush=True)
