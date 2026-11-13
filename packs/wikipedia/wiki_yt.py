#!/usr/bin/env python3
"""Count distinct enwiki articles (ns 0) citing each YouTube video.
Usage: wiki_yt.py <externallinks.sql.gz> <page.sql.gz> <catalog_vids.json> <outdir>
Streams the MySQL dumps through pigz; no MySQL needed."""
import sys, re, json, subprocess, time, collections

EL, PG, CAT, OUT = sys.argv[1:5]
t0 = time.time()
stats = collections.OrderedDict()

def stream(path, grep=None):
    # dumps are one tuple per line: "(1,2,...),"
    cmd = f"pigz -dc '{path}'" + (f" | LC_ALL=C grep -F '{grep}'" if grep else "")
    p = subprocess.Popen(["bash", "-c", cmd], stdout=subprocess.PIPE, bufsize=1 << 24)
    for line in p.stdout:
        if line.startswith(b"("):
            yield line
    p.wait()

def unesc(b):
    return re.sub(rb"\\(.)", lambda m: {b"n": b"\n", b"t": b"\t", b"0": b"\0"}.get(m.group(1), m.group(1)), b).decode("utf-8", "replace")

# ---- 1. externallinks: rows on YouTube domains ----
# tuple: (el_id,el_from,'el_to_domain_index','el_to_path')
DOM = rb"((?:https?:)?//(?:com\.youtube(?:-nocookie)?|be\.youtu)\.[^']*)"
el_re = re.compile(rb"\((\d+),(\d+),'" + DOM + rb"','((?:[^'\\]|\\.)*)'\)")
# 11-char id; the 11th char of a real id is always one of [AEIMQUYcgkosw048].
# Trailing junk from malformed wiki markup ("ID--", "IDAnd") is tolerated and counted.
ID = r"([A-Za-z0-9_-]{10}[AEIMQUYcgkosw048])"
TAIL = re.compile(r"[A-Za-z0-9_-]")
yt_res = [re.compile(r"[?&#!]v=" + ID), re.compile(r"[?&]video_id=" + ID),
          re.compile(r"^/(?:embed|v|e|shorts|live|watch)/" + ID)]
ytbe_re = re.compile(r"^/" + ID)

total_el_rows = 0
yt_rows = 0
dom_counter = collections.Counter()
vid_rows = 0
lenient = 0
pairs = collections.Counter()          # (vid, page_id) -> link rows
nonvid_kind = collections.Counter()
for line in stream(EL, grep="youtu"):
    for m in el_re.finditer(line):
        yt_rows += 1
        dom = m.group(3).decode()
        dom_counter[dom] += 1
        path = unesc(m.group(4))
        vid = None
        if ".be.youtu." in "." + dom.split("//", 1)[1]:
            mm = ytbe_re.match(path)
            vid = mm.group(1) if mm else None
        else:
            for r in yt_res:
                mm = r.search(path)
                if mm:
                    vid = mm.group(1); break
        if vid is None:
            seg = path.split("?")[0].strip("/").split("/")[0] or "(root)"
            nonvid_kind[seg[:20]] += 1
            continue
        vid_rows += 1
        if mm.end() < len(path) and TAIL.match(path, mm.end()):
            lenient += 1
        pairs[(vid, int(m.group(2)))] += 1
stats["youtube_domain_rows"] = yt_rows
stats["youtube_rows_with_video_id"] = vid_rows
stats["rows_with_trailing_junk_after_id"] = lenient
stats["distinct_vid_page_pairs_all_ns"] = len(pairs)
stats["top_youtube_domain_indexes"] = dom_counter.most_common(12)
stats["top_non_video_path_kinds"] = nonvid_kind.most_common(15)
need = {p for _, p in pairs}
stats["distinct_pages_citing_any_vid_all_ns"] = len(need)
stats["t_externallinks_s"] = round(time.time() - t0)
print(json.dumps(stats, indent=1), flush=True)

# ---- 2. page: page_id -> (ns, title, len) for needed ids ----
# (page_id,page_namespace,'page_title',page_is_redirect,page_is_new,page_random,'touched','links_updated',page_latest,page_len,...
pg_re = re.compile(rb"\((\d+),(-?\d+),'((?:[^'\\]|\\.)*)',(\d),\d,[0-9.e+-]+,'[^']*',(?:'[^']*'|NULL),\d+,(\d+),")
pages = {}
total_pages = 0
ns_counter = collections.Counter()
for line in stream(PG):
    for m in pg_re.finditer(line):
        total_pages += 1
        pid = int(m.group(1))
        if pid in need:
            ns = int(m.group(2))
            ns_counter[ns] += 1
            pages[pid] = (ns, unesc(m.group(3)).replace("_", " "), int(m.group(5)), int(m.group(4)))
stats["page_rows_parsed"] = total_pages
stats["citing_pages_found_in_page_table"] = len(pages)
stats["citing_pages_by_namespace"] = sorted(ns_counter.items(), key=lambda x: -x[1])[:12]
stats["t_page_s"] = round(time.time() - t0)

# ---- 3. join, ns 0 only ----
per_vid = collections.defaultdict(list)   # vid -> [(links, len, title)]
art_pages = set()
for (vid, pid), n in pairs.items():
    pg = pages.get(pid)
    if pg is None or pg[0] != 0:
        continue
    art_pages.add(pid)
    per_vid[vid].append((n, pg[2], pg[1]))
stats["ns0_articles_citing_any_vid"] = len(art_pages)
stats["vids_cited_by_ge1_article"] = len(per_vid)
dist = collections.Counter()
for v, l in per_vid.items():
    k = len(l); dist["1" if k == 1 else "2" if k == 2 else "3-4" if k <= 4 else "5-9" if k <= 9 else "10-49" if k <= 49 else "50+"] += 1
stats["articles_per_vid_distribution"] = dist

def entry(vid):
    l = sorted(per_vid[vid], key=lambda x: (-x[0], -x[1], x[2]))
    return {"articles": len(l), "titles": [t for _, _, t in l[:5]]}

catalog = set(json.load(open(CAT)))
counts = {v: entry(v) for v in per_vid}
s = json.dumps(counts, ensure_ascii=False, separators=(",", ":"))
stats["wiki_counts_full_bytes"] = len(s.encode())
if len(s.encode()) > 150e6:
    counts = {v: e for v, e in counts.items() if e["articles"] >= 2 or v in catalog}
    s = json.dumps(counts, ensure_ascii=False, separators=(",", ":"))
    stats["wiki_counts_trimmed"] = True
stats["wiki_counts_vids_written"] = len(counts)
stats["wiki_counts_bytes"] = len(s.encode())
open(f"{OUT}/wiki_counts.json", "w").write(s)

# catalogue overlap
ov = {v: len(per_vid[v]) for v in sorted(catalog) if v in per_vid}
cd = collections.Counter()
for n in ov.values():
    cd["1" if n == 1 else "2" if n == 2 else "3-4" if n <= 4 else "5-9" if n <= 9 else "10+"] += 1
top_cat = sorted(ov.items(), key=lambda x: -x[1])[:25]
summary = {"catalog_vids": len(catalog), "cited_by_ge1_article": len(ov),
           "share_cited": round(len(ov) / len(catalog), 4),
           "cited_by_ge2": sum(1 for n in ov.values() if n >= 2),
           "cited_by_ge5": sum(1 for n in ov.values() if n >= 5),
           "distribution": dict(cd),
           "top25": [{"vid": v, "articles": n, "titles": counts.get(v, entry(v))["titles"]} for v, n in top_cat]}
json.dump({"summary": summary, "counts": ov}, open(f"{OUT}/catalog_overlap.json", "w"), ensure_ascii=False, indent=1)
stats["catalog_summary"] = {k: v for k, v in summary.items() if k != "top25"}

top = sorted((v for v in per_vid if v not in catalog), key=lambda v: (-len(per_vid[v]), v))[:2000]
json.dump([{"vid": v, **entry(v)} for v in top], open(f"{OUT}/top_not_in_catalog.json", "w"), ensure_ascii=False, indent=1)
stats["top_not_in_catalog_min_articles"] = len(per_vid[top[-1]]) if top else 0
stats["top_not_in_catalog_max_articles"] = len(per_vid[top[0]]) if top else 0
stats["runtime_s"] = round(time.time() - t0)
json.dump(stats, open(f"{OUT}/stats.json", "w"), indent=1)
print(json.dumps(stats, indent=1))
