"""Export the catalogue as a public data release, without YouTube's own metadata.

    .venv/bin/python -I scripts/export_release.py [--data app/data] [--hackertube data/raw/hackertube-data]
                                                  [--out dist] [--name hackertube-ai-dataset-YYYY-MM-DD]

Writes dist/<name>/ with two folders and a zip of the lot:

  hackertube-format/   drop-in compatible with the HackerTube data download (https://hackertube.org/data): the same
                       files and fields, plus the sources HackerTube AI adds (Stack Exchange, the open web, Wikipedia).
  app-data/            this app's own app/data files, ready to drop into a fork (add YouTube metadata yourself with
                       scripts/fetch_youtube_meta.py and scripts/add_youtube_meta.py).

YouTube's API terms don't allow redistributing its metadata, so durations, view counts, publish dates and channel ids
are left out (titles and channel names stay, as in HackerTube's own download). Quoted comment text has email addresses
and anything that looks like a key or token replaced, since people sometimes paste those into forum posts.
"""
import argparse, datetime, hashlib, json, os, re, shutil, sys, zipfile

import numpy as np

YOUTUBE_ONLY = ("dur", "views", "published", "channel_id")
REDACT = [(re.compile(p), r) for p, r in (
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", "[redacted key]"),
    (r"AIza[0-9A-Za-z_\-]{35}", "[redacted key]"),
    (r"\b(sk|rk|pk)_(live|test)_[0-9A-Za-z]{10,}", "[redacted key]"),
    (r"\bwhsec_[0-9A-Za-z]{10,}", "[redacted key]"),
    (r"\bsk-(ant-)?[A-Za-z0-9_\-]{20,}", "[redacted key]"),
    (r"\b(ghp|gho|ghu|ghs|github_pat)_[0-9A-Za-z_]{20,}", "[redacted key]"),
    (r"\bxox[baprs]-[0-9A-Za-z\-]{10,}", "[redacted key]"),
    (r"\bhf_[A-Za-z0-9]{30,}", "[redacted key]"),
    (r"\bAKIA[0-9A-Z]{16}\b", "[redacted key]"),
    (r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]*", "[redacted token]"),
    (r"(?i)\bbearer\s+[A-Za-z0-9._\-]{20,}", "Bearer [redacted token]"),
    (r"\b\d{6,}-[0-9a-z]{20,}\.apps\.googleusercontent\.com\b", "[redacted client id]"),
    (r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", "[email]"),
)]


def scrub(s):
    if not isinstance(s, str):
        return s
    for p, r in REDACT:
        s = p.sub(r, s)
    return s


def jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def load_jsonl(path):
    return [json.loads(line) for line in open(path, encoding="utf-8")] if os.path.exists(path) else []


def categories(cats, tax):
    out = []
    for cid in cats or []:
        if cid in tax:
            out.append(dict(tax[cid]))
    return out


def his_hn_stats(v, ments):
    """HackerTube's `hn` block for one video: stance counts from its labelled HN comments, the rest from the
    catalogue's (newer) HN figures."""
    hn = (v.get("comm") or {}).get("hn")
    if not hn:
        return {"mentions": 0, "recommenders": 0, "rec_months": 0, "strong_recs": 0, "stances": {},
                "first_year": None, "last_year": None}
    stances = {}
    for m in ments:
        stances[m["stance"]] = stances.get(m["stance"], 0) + 1
    return {"mentions": len(ments) or hn.get("mentions", 0), "recommenders": hn.get("recommenders", 0),
            "rec_months": hn.get("rec_months", 0), "strong_recs": stances.get("strong_rec", 0), "stances": stances,
            "first_year": hn.get("first_year"), "last_year": hn.get("last_year")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="app/data")
    ap.add_argument("--hackertube", default="data/raw/hackertube-data", help="the original HackerTube download, for "
                    "quotes and co-recommendation counts on HN videos (optional)")
    ap.add_argument("--packs", default="packs", help="community packs, for their full labelled mentions (optional)")
    ap.add_argument("--out", default="dist")
    ap.add_argument("--name", default="hackertube-ai-dataset-" + datetime.date.today().isoformat())
    a = ap.parse_args()

    root = os.path.join(a.out, a.name)
    if os.path.exists(root):
        shutil.rmtree(root)
    app_out, ht_out = os.path.join(root, "app-data"), os.path.join(root, "hackertube-format")
    os.makedirs(app_out)
    os.makedirs(ht_out)

    d = a.data
    videos = json.load(open(f"{d}/videos.json", encoding="utf-8"))
    mentions = json.load(open(f"{d}/mentions.json", encoding="utf-8"))
    taxonomy = json.load(open(f"{d}/taxonomy.json", encoding="utf-8"))
    emb, title_emb = np.load(f"{d}/embeddings.npy"), np.load(f"{d}/title_emb.npy")
    assert len(videos) == len(emb) == len(title_emb), "videos and embeddings disagree"

    # ---- app-data: this app's own files, minus YouTube's metadata, with comment text scrubbed
    clean = [{k: x for k, x in v.items() if k not in YOUTUBE_ONLY} for v in videos]
    for v in clean:
        if isinstance(v.get("quote"), dict):
            v["quote"] = dict(v["quote"], text=scrub(v["quote"].get("text")))
    clean_m = {vid: [dict(m, text=scrub(m.get("text")), why=scrub(m.get("why")), quote=scrub(m.get("quote", "")))
                     for m in ms] for vid, ms in mentions.items()}
    json.dump(clean, open(f"{app_out}/videos.json", "w", encoding="utf-8"), ensure_ascii=False)
    json.dump(clean_m, open(f"{app_out}/mentions.json", "w", encoding="utf-8"), ensure_ascii=False)
    for name in ("related.json", "authors.json", "taxonomy.json", "communities.json", "topic_intros.json"):
        if os.path.exists(f"{d}/{name}"):
            shutil.copy(f"{d}/{name}", f"{app_out}/{name}")
    np.save(f"{app_out}/embeddings.npy", emb.astype(np.float16))
    np.save(f"{app_out}/title_emb.npy", title_emb.astype(np.float16))

    # ---- hackertube-format: the HackerTube download's files and fields, extended
    tax = {}
    for area in taxonomy["areas"]:
        for c in area["categories"]:
            tax[c["id"]] = {"id": c["id"], "name": c["name"], "area": area["id"], "area_name": area["name"]}
    orig_v = {v["vid"]: v for v in load_jsonl(os.path.join(a.hackertube, "videos.jsonl"))}
    orig_rel = {r["vid"]: r for r in load_jsonl(os.path.join(a.hackertube, "related.jsonl"))}
    in_cat = {v["vid"] for v in videos}
    # every labelled mention we have: HackerTube's HN comments, then each community pack's (newer HN, Stack Exchange)
    hn_by_vid, other_by_vid, seen = {}, {}, set()
    for m in load_jsonl(os.path.join(a.hackertube, "mentions.jsonl")):
        if m["vid"] in in_cat and m["hn_comment_id"] not in seen:
            seen.add(m["hn_comment_id"])
            hn_by_vid.setdefault(m["vid"], []).append(dict(m, quote=scrub(m.get("quote")), text=scrub(m.get("text"))))
    for pack in sorted(os.listdir(a.packs)) if os.path.isdir(a.packs) else ():
        for m in load_jsonl(os.path.join(a.packs, pack, "mentions.jsonl")):
            if m.get("vid") not in in_cat:
                continue
            if m.get("community") == "hn":
                cid = int(m["id"])
                if cid in seen:
                    continue
                seen.add(cid)
                hn_by_vid.setdefault(m["vid"], []).append({
                    "hn_comment_id": cid, "hn_url": m.get("url") or f"https://news.ycombinator.com/item?id={cid}",
                    "vid": m["vid"], "author": m.get("author"), "month": m.get("month"), "story_title": m.get("context"),
                    "stance": m.get("stance"), "why": scrub(m.get("why") or ""), "quote": scrub(m.get("quote") or ""),
                    "text": scrub(m.get("text") or "")})
            else:
                src = m.get("community") or pack
                other_by_vid.setdefault(m["vid"], []).append({
                    "source": src, "id": m.get("id"), "url": m.get("url"), "vid": m["vid"], "author": m.get("author"),
                    "month": m.get("month"), "context": m.get("context"), "stance": m.get("stance"),
                    "why": scrub(m.get("why") or ""), "quote": scrub(m.get("quote") or ""), "text": scrub(m.get("text") or ""),
                    "license": "CC BY-SA 4.0" if str(src).startswith("se:") else None})

    rows, hn_rows, other_rows = [], [], []
    for v in sorted(clean, key=lambda x: -float(x.get("score") or 0)):
        hn_ms = hn_by_vid.get(v["vid"], [])
        comm = v.get("comm") or {}
        why_list = v.get("why_list") or []
        rows.append({
            "vid": v["vid"], "url": f"https://www.youtube.com/watch?v={v['vid']}",
            "title": v["title"], "channel": v["channel"], "summary": v.get("summary") or "",
            "why_hn": (comm.get("hn") or {}).get("why", ""),
            "content_type": v.get("type") or "other", "level": v.get("level") or "general",
            "shelf_life": v.get("shelf") or "evergreen", "primary_topic": v.get("topic") or "",
            "tags": v.get("tags") or [], "categories": categories(v.get("cats"), tax), "quote": v.get("quote"),
            "hn": his_hn_stats(v, hn_ms), "score": v.get("score"),
            # what HackerTube AI adds
            "why": (why_list[0]["why"] if why_list else v.get("why_hn")) or "",
            "primary_source": v.get("primary"),
            "sources": {c: {k: s.get(k) for k in ("why", "recommenders", "rec_months", "mentions", "first_year", "last_year")}
                        for c, s in comm.items()},
            "web": {k: x for k, x in (v.get("web") or {}).items() if k in
                    ("domains", "months", "first_seen", "last_seen", "top_domains", "reputable", "why", "kind", "area")} or None,
            "wikipedia": v.get("wiki") or None,
        })
        hn_rows.extend(hn_ms)
        other_rows.extend(other_by_vid.get(v["vid"], []))
    jsonl(f"{ht_out}/videos.jsonl", rows)
    jsonl(f"{ht_out}/mentions.jsonl", hn_rows)
    jsonl(f"{ht_out}/mentions_other.jsonl", other_rows)
    jsonl(f"{ht_out}/related.jsonl", [r for vid, r in orig_rel.items() if vid in in_cat])
    shutil.copy(f"{d}/taxonomy.json", f"{ht_out}/taxonomy.json")
    order = {v["vid"]: i for i, v in enumerate(videos)}
    ids = [r["vid"] for r in rows]
    np.save(f"{ht_out}/embeddings.npy", emb[[order[i] for i in ids]].astype(np.float16))
    json.dump(ids, open(f"{ht_out}/embedding_ids.json", "w"))

    here = os.path.dirname(os.path.abspath(__file__))
    for doc in ("DATA.md", "LICENSE-DATA.md"):
        src = os.path.join(os.path.dirname(here), doc)
        if os.path.exists(src):
            shutil.copy(src, os.path.join(root, doc))
    counts = {"videos": len(rows), "hn_mentions": len(hn_rows), "other_mentions": len(other_rows),
              "by_primary_source": {}}
    for r in rows:
        k = "web" if r["primary_source"] == "web" else ("hn" if r["primary_source"] == "hn" else
                                                       "stackexchange" if str(r["primary_source"]).startswith("se:") else "other")
        counts["by_primary_source"][k] = counts["by_primary_source"].get(k, 0) + 1
    manifest = {"name": a.name, "exported": datetime.date.today().isoformat(), "counts": counts, "files": {}}
    for base, _, files in os.walk(root):
        for f in sorted(files):
            p = os.path.join(base, f)
            manifest["files"][os.path.relpath(p, root)] = {"bytes": os.path.getsize(p),
                                                           "sha256": hashlib.sha256(open(p, "rb").read()).hexdigest()}
    json.dump(manifest, open(f"{root}/MANIFEST.json", "w"), indent=1)
    print(json.dumps(counts))
    # one zip per folder, so people download only the format they need; the docs go in both
    for part in ("hackertube-format", "app-data"):
        z = os.path.join(a.out, f"{a.name}-{part}.zip")
        with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
            for base, _, files in os.walk(os.path.join(root, part)):
                for f in sorted(files):
                    zf.write(os.path.join(base, f), os.path.join(a.name, part, f))
            for f in ("DATA.md", "LICENSE-DATA.md", "MANIFEST.json"):
                if os.path.exists(os.path.join(root, f)):
                    zf.write(os.path.join(root, f), os.path.join(a.name, f))
        print(z, round(os.path.getsize(z) / 1e6, 1), "MB")


if __name__ == "__main__":
    sys.exit(main())
