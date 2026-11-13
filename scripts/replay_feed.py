"""Replay someone's Pro feed from their stored profile, to check a ranking change against real people before
shipping it.

    GCP_PROJECT=your-project .venv/bin/python -I scripts/replay_feed.py USER_ID [USER_ID ...]

Reads the user documents from production Firestore (read only, with your gcloud login) and ranks them with the
local code and catalogue. Prints the first screen with the reason each pick shows. Nothing is written anywhere.
"""
import copy, json, os, subprocess, sys, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.update({"STORE": "memory", "HT_NOTES": "1"})
os.environ.pop("DATA_BUCKET", None)

from app import main as M  # noqa: E402

PROJECT = os.environ.get("GCP_PROJECT", "")
DOC = f"https://firestore.googleapis.com/v1/projects/{PROJECT}/databases/(default)/documents/ht_users/"


def plain(v):
    if "mapValue" in v:
        return {k: plain(x) for k, x in v["mapValue"].get("fields", {}).items()}
    if "arrayValue" in v:
        return [plain(x) for x in v["arrayValue"].get("values", [])]
    if "integerValue" in v:
        return int(v["integerValue"])
    if "nullValue" in v:
        return None
    return list(v.values())[0]


def fetch(uid, token):
    req = urllib.request.Request(DOC + uid, headers={"Authorization": f"Bearer {token}"})
    fields = json.load(urllib.request.urlopen(req)).get("fields", {})
    u = {k: plain(v) for k, v in fields.items() if k not in ("email", "name", "picture", "payout")}
    u["uid"] = uid
    return u


def main(uids):
    token = subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True, check=True).stdout.strip()
    for uid in uids:
        u = fetch(uid, token)
        d = M.personal_data(copy.deepcopy(u))
        if not d:
            print(f"{uid}: no taste profile yet")
            continue
        print(f"\n{uid}  sources: {[k for k, v in (u.get('vecs') or {}).items() if v]}  follows: {len(u.get('yt_channels_matched') or [])} channels")
        for k, v in enumerate(d["feed"][:12], 1):
            print(f"  {k:2} {v['title'][:56]:56} {v['channel'][:20]:20} {v['dur'] // 60:>3}m  {d['reasons'].get(v['vid'], '')}")


if __name__ == "__main__":
    if len(sys.argv) < 2 or not PROJECT:
        sys.exit(__doc__)
    main(sys.argv[1:])
