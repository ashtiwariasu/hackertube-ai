"""Build the app's catalogue: HackerTube's HN dataset + community packs (packs/*/videos.jsonl),
fresh HN mentions (packs/hn-fresh), Wikipedia citation counts (packs/wikipedia), the open-web pack
(packs/web, Common Crawl link counts), and YouTube metadata.

Usage: .venv/bin/python -I scripts/build_catalog.py [--refresh-meta] [--web-pack DIR]
  --refresh-meta  refetch YouTube metadata for every video (YouTube API data must be refreshed within 30 days)
  --web-pack DIR  read the open-web pack from DIR instead of packs/web (e.g. packs/web-sample for a local run)
YOUTUBE_API_KEY must be set when metadata is missing or being refreshed."""
import collections, glob, json, math, os, re, sys, time, urllib.parse, urllib.request
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # -I drops the repo root from sys.path
if os.environ.get("HT_APP_ROOT"):  # the refresh job runs this from a bundle that has no app/ package; it names where app/ lives
    sys.path.insert(0, os.environ["HT_APP_ROOT"])
from app.web_source import WEB_COMM, WEB_LABEL, WEB_SHORT, apply_web, derive_web_only, load_web_pack, web_score, web_topic_cats  # noqa: E402

RAW = "data/raw/hackertube-data"
OUT = "app/data"
META = "data/youtube_meta.jsonl"
PACKS = "packs"
WEB_PACK = sys.argv[sys.argv.index("--web-pack") + 1] if "--web-pack" in sys.argv else f"{PACKS}/web"
ORDER = {"strong_rec": 0, "rec": 1, "reference": 2, "critical": 3, "joke": 4}
SE_NAMES = {"stackoverflow": "Stack Overflow", "superuser": "Super User", "serverfault": "Server Fault",
            "askubuntu": "Ask Ubuntu", "mathoverflow": "MathOverflow", "math": "Mathematics", "diy": "DIY",
            "scifi": "Science Fiction & Fantasy", "rpg": "RPG", "ux": "User Experience", "gamedev": "Game Development"}


SE_SITES = {}
if os.path.exists("packs/stackexchange/scripts/se_sites.json"):
    for dom, info in json.load(open("packs/stackexchange/scripts/se_sites.json")).items():
        SE_SITES[info.get("api") or dom.split(".")[0]] = (info.get("name") or dom, dom)
BLOCK = {"dQw4w9WgXcQ"}  # link-bait and memes that pass the rule through sheer repetition


def community_label(c):
    if c.startswith("se:") and c[3:] in SE_SITES:
        name, dom = SE_SITES[c[3:]]
        return f"{name} Stack Exchange" if dom.endswith("stackexchange.com") and "Stack Exchange" not in name else name
    if c == "hn":
        return "Hacker News"
    if c == "bluesky":
        return "Bluesky"
    if c.startswith("se:"):
        site = c[3:].replace(".stackexchange.com", "").replace(".com", "")
        name = SE_NAMES.get(site, site.replace("-", " ").title())
        return name if site in ("stackoverflow", "superuser", "serverfault", "askubuntu", "mathoverflow") else f"{name} Stack Exchange"
    return c


def community_short(c):
    if c == "se:stackoverflow":
        return "SO"
    if c.startswith("se:") and c[3:] in SE_SITES:
        name, dom = SE_SITES[c[3:]]
        return f"{name} SE" if dom.endswith("stackexchange.com") else name
    if c == "hn":
        return "HN"
    if c.startswith("se:"):
        site = c[3:].replace(".stackexchange.com", "")
        return {"stackoverflow": "SO"}.get(site, SE_NAMES.get(site, site.title()) + " SE")
    return community_label(c)


def dur_seconds(iso):
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def jsonl(path):
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


# ---------------- gather sources ----------------
hn_videos = jsonl(f"{RAW}/videos.jsonl")
hn_mentions = jsonl(f"{RAW}/mentions.jsonl")
fresh_mentions = jsonl(f"{PACKS}/hn-fresh/mentions.jsonl")
fresh_updates = {r["vid"]: r for r in jsonl(f"{PACKS}/hn-fresh/updated_stats.jsonl")}
pack_videos, pack_mentions = [], []
for d in sorted(glob.glob(f"{PACKS}/*/")):
    name = os.path.basename(d.rstrip("/"))
    if name in ("wikipedia", "bluesky-raw") or name == "web" or name.startswith("web-"):
        continue  # web packs have no community field; they are read separately below
    pack_videos += jsonl(os.path.join(d, "videos.jsonl"))
    if not d.rstrip("/").endswith("hn-fresh"):
        pack_mentions += jsonl(os.path.join(d, "mentions.jsonl"))
wiki = json.load(open(f"{PACKS}/wikipedia/wiki_counts.json")) if os.path.exists(f"{PACKS}/wikipedia/wiki_counts.json") else {}
web = load_web_pack(WEB_PACK)  # {vid: {domains, months, first_seen, last_seen, top_domains, why?}}
if web:
    print("web pack", WEB_PACK, "|", len(web), "videos")

# ---------------- YouTube metadata ----------------
meta = {r["vid"]: r["item"] for r in jsonl(META)}
all_vids = list(dict.fromkeys([v["vid"] for v in hn_videos] + [v["vid"] for v in pack_videos] + list(web)))
todo = all_vids if "--refresh-meta" in sys.argv else [v for v in all_vids if v not in meta]
if todo:
    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        sys.exit(f"{len(todo)} videos need YouTube metadata: set YOUTUBE_API_KEY")
    for i in range(0, len(todo), 50):
        ids = todo[i:i + 50]
        q = urllib.parse.urlencode({"part": "snippet,contentDetails,statistics,status", "id": ",".join(ids), "key": key})
        for attempt in range(5):
            try:
                with urllib.request.urlopen("https://www.googleapis.com/youtube/v3/videos?" + q, timeout=60) as r:
                    items = {it["id"]: it for it in json.load(r)["items"]}
                break
            except Exception as e:  # noqa: BLE001
                print("retry", attempt, e)
                time.sleep(2 * (attempt + 1))
        else:
            sys.exit("YouTube API kept failing")
        for v in ids:
            meta[v] = items.get(v)
        time.sleep(0.1)
    with open(META, "w", encoding="utf-8") as f:
        for v, it in meta.items():
            f.write(json.dumps({"vid": v, "item": it, "fetched": time.strftime("%Y-%m-%d")}) + "\n")
    print("fetched metadata for", len(todo))


def usable(vid):
    it = meta.get(vid)
    return bool(it) and it.get("status", {}).get("embeddable", True) and it.get("status", {}).get("privacyStatus") == "public"


def base_record(vid, card):
    it = meta[vid]
    sn, cd, st = it.get("snippet", {}), it.get("contentDetails", {}), it.get("statistics", {})
    return {"vid": vid, "title": card.get("title") or sn.get("title", ""), "channel": card.get("channel") or sn.get("channelTitle", ""),
            "summary": card.get("summary", ""), "type": card.get("content_type", "other"), "level": card.get("level", "general"),
            "shelf": card.get("shelf_life", "evergreen"), "topic": card.get("primary_topic", ""), "tags": card.get("tags", []),
            "cats": [], "quote": card.get("quote"),
            "dur": dur_seconds(cd.get("duration")), "views": int(st.get("viewCount", 0) or 0),
            "published": (sn.get("publishedAt") or "")[:10], "channel_id": sn.get("channelId", ""), "comm": {}}


# ---------------- HN base ----------------
videos, dropped = {}, 0
for v in hn_videos:
    if not usable(v["vid"]):
        dropped += 1
        continue
    rec = base_record(v["vid"], {**v, "categories": None})
    rec["cats"] = [c["id"] for c in v["categories"]]
    stats = dict(v["hn"])
    score = v["score"]
    if v["vid"] in fresh_updates:
        stats.update(fresh_updates[v["vid"]]["stats"])
        score = fresh_updates[v["vid"]].get("score", score)
    rec["comm"]["hn"] = {"why": v["why_hn"], "recommenders": stats.get("recommenders", 0), "rec_months": stats.get("rec_months", 0),
                         "mentions": stats.get("mentions", 0), "first_year": stats.get("first_year"),
                         "last_year": stats.get("last_year"), "score": score}
    videos[v["vid"]] = rec

# ---------------- packs (new HN videos, Stack Exchange, Bluesky, ...) ----------------
added = collections.Counter()
hn_sorted = sorted(v["score"] for v in hn_videos)
by_comm = collections.defaultdict(list)
for v in pack_videos:
    by_comm[v.get("community", "hn")].append(v.get("score", 0))
for c in by_comm:
    by_comm[c].sort()


def to_hn_scale(c, score):
    """Map a community's score onto HN's distribution by percentile, so big sites don't drown the rest."""
    if c == "hn":
        return score
    arr = by_comm[c]
    pct = (np.searchsorted(arr, score, side="right") - 0.5) / max(len(arr), 1)
    return round(float(hn_sorted[min(int(pct * len(hn_sorted)), len(hn_sorted) - 1)]), 2)


for v in pack_videos:
    vid, c = v["vid"], v.get("community", "hn")
    if vid in BLOCK or not usable(vid):
        continue
    if vid not in videos:
        videos[vid] = base_record(vid, v)
        videos[vid]["cats"] = [x["id"] if isinstance(x, dict) else x for x in v.get("categories", [])][:3]
        added[c] += 1
    st = v.get("stats", {})
    videos[vid]["comm"][c] = {"why": v.get("why", ""), "recommenders": st.get("recommenders", 0), "rec_months": st.get("rec_months", 0),
                              "mentions": st.get("mentions", 0), "first_year": st.get("first_year"),
                              "last_year": st.get("last_year"), "score": to_hn_scale(c, v.get("score", 0)),
                              "raw_score": v.get("score", 0)}

# ---------------- open web (Common Crawl link counts) ----------------
# A video that only the web pack knows gets a base record from YouTube metadata (no card, no categories);
# the derived fields below give it the same shape as every other video.
TOPIC_IDS = {c["id"] for a in json.load(open(f"{RAW}/taxonomy.json"))["areas"] for c in a["categories"]}
web_only_added = apply_web(videos, web, lambda vid: base_record(vid, {}) if vid not in BLOCK and usable(vid) else None)
if web_only_added:
    added[WEB_COMM] = web_only_added

# ---------------- derived fields ----------------
for vid, v in videos.items():
    comms = sorted(v["comm"].items(), key=lambda kv: -kv[1]["score"])
    if not comms:
        derive_web_only(v)
        web_topic_cats(v, TOPIC_IDS)
    else:
        v["primary"] = comms[0][0]
        scores = [s["score"] for _, s in comms]
        v["score"] = round(scores[0] + 0.3 * sum(scores[1:]), 2)
    w = wiki.get(vid) or {}
    if w.get("articles"):
        v["wiki"] = {"articles": w["articles"], "titles": w.get("titles", [])[:3]}
        v["score"] = round(v["score"] + 0.5 * min(math.log2(1 + w["articles"]), 3), 2)
    if comms and v.get("web"):
        v["score"] = round(v["score"] + web_score(v["web"], has_other_sources=True), 2)
    if not comms:
        continue
    v["recs"] = sum(s["recommenders"] for _, s in comms)
    top = comms[0]
    v["badge"] = f"{top[1]['recommenders']} {community_short(top[0])} recs" if top[1]["recommenders"] else ""
    v["why_list"] = [{"c": c, "label": community_label(c), **s} for c, s in comms]
    v["why_hn"] = comms[0][1]["why"]
    v["hn"] = {"recommenders": v["recs"], "rec_months": max(s["rec_months"] for _, s in comms),
               "first_year": min((s["first_year"] for _, s in comms if s["first_year"]), default=None),
               "last_year": max((s["last_year"] for _, s in comms if s["last_year"]), default=None)}

videos = sorted(videos.values(), key=lambda v: -v["score"])
keep = {v["vid"] for v in videos}

# ---------------- mentions ----------------
mentions = collections.defaultdict(list)
for m in hn_mentions:
    if m["vid"] in keep and m["stance"] != "joke":
        mentions[m["vid"]].append({"id": m["hn_comment_id"], "url": f"https://news.ycombinator.com/item?id={m['hn_comment_id']}",
                                   "c": "hn", "author": m["author"], "month": m["month"], "story": m["story_title"],
                                   "stance": m["stance"], "why": m["why"], "text": m["text"][:700]})
for m in fresh_mentions + pack_mentions:
    if m["vid"] in keep and m.get("stance") != "joke":
        mentions[m["vid"]].append({"id": m["id"], "url": m.get("url", ""), "c": m.get("community", "hn"), "author": m.get("author", ""),
                                   "month": m.get("month", ""), "story": m.get("context", ""), "stance": m.get("stance", "reference"),
                                   "why": m.get("why", ""), "text": (m.get("text") or "")[:700]})
for vid in mentions:
    seen = set()
    uniq = [m for m in mentions[vid] if not (str(m["id"]) in seen or seen.add(str(m["id"])))]
    uniq.sort(key=lambda m: (ORDER.get(m["stance"], 5), m["month"]))
    mentions[vid] = uniq[:10]
for m in mentions.values():
    for x in m:
        x["clabel"] = community_label(x["c"])

authors = collections.defaultdict(list)
for m in hn_mentions + fresh_mentions:
    a = (m.get("author") or "").lower()
    if m["vid"] in keep and m.get("stance") in ("rec", "strong_rec") and a and m["vid"] not in authors[a]:
        authors[a].append(m["vid"])

related = {}
for r in jsonl(f"{RAW}/related.jsonl"):
    if r["vid"] in keep:
        related[r["vid"]] = [x["vid"] for x in r["related"] if x["vid"] in keep][:12]

# ---------------- embeddings ----------------
# Vectors are cached by a hash of the exact text embedded (data/emb_cache.npz, kept in the refresh bundle), so a
# rebuild only embeds videos whose text is new or changed. The original dataset's vectors are used as before.
import hashlib  # noqa: E402
from fastembed import TextEmbedding  # noqa: E402
model = TextEmbedding("BAAI/bge-small-en-v1.5")
tax = json.load(open(f"{RAW}/taxonomy.json"))
cat_names = {c["id"]: c["name"] for a in tax["areas"] for c in a["categories"]}
old = np.load(f"{RAW}/embeddings.npy").astype(np.float32)
old_ids = {v: i for i, v in enumerate(json.load(open(f"{RAW}/embedding_ids.json")))}
EMB_CACHE = "data/emb_cache.npz"
cache = {}
if os.path.exists(EMB_CACHE):
    z = np.load(EMB_CACHE)
    cache = dict(zip(z["keys"].tolist(), z["vecs"]))
used, embedded = set(), 0


def embed_cached(texts):
    global embedded
    keys = [hashlib.sha1(t.encode("utf-8")).hexdigest()[:20] for t in texts]
    miss = [i for i, k in enumerate(keys) if k not in cache]
    if miss:
        arr = np.array(list(model.embed([texts[i] for i in miss])), dtype=np.float32)
        arr /= np.linalg.norm(arr, axis=1, keepdims=True) + 1e-9
        for j, i in enumerate(miss):
            cache[keys[i]] = arr[j].astype(np.float16)
        embedded += len(miss)
    used.update(keys)
    return np.stack([cache[k].astype(np.float32) for k in keys]) if keys else np.zeros((0, 384), np.float32)


need = [v for v in videos if v["vid"] not in old_ids]
new_vecs = {}
if need:
    texts = [f"{v['title']}. {v['channel']}. {v['topic']}. {', '.join(v['tags'][:8])}. "
             f"{', '.join(cat_names.get(c, '') for c in v['cats'])}. {v['summary']} "
             f"{' '.join(w['why'] for w in v['why_list'])} {(v.get('web') or {}).get('why', '')}" for v in need]
    arr = embed_cached(texts)
    new_vecs = {v["vid"]: arr[i] for i, v in enumerate(need)}
emb = np.stack([old[old_ids[v["vid"]]] if v["vid"] in old_ids else new_vecs[v["vid"]] for v in videos])
np.save(f"{OUT}/embeddings.npy", emb.astype(np.float16))
t = embed_cached([f"{v['title']} {v['channel']}" for v in videos])
np.save(f"{OUT}/title_emb.npy", t.astype(np.float16))
keep = sorted(used)  # drop vectors no video uses any more, so the cache never outgrows the catalogue
np.savez(EMB_CACHE, keys=np.array(keep), vecs=np.stack([cache[k] for k in keep]) if keep else np.zeros((0, 384), np.float16))
print("embeddings: computed", embedded, "| from cache", len(used) - embedded if len(used) >= embedded else 0)

communities = collections.Counter(c for v in videos for c in v["comm"])
comm_meta = {c: {"label": community_label(c), "short": community_short(c), "videos": n} for c, n in communities.items()}
web_n = sum(1 for v in videos if v.get("web"))
web_curated = sum(1 for v in videos if (v.get("web") or {}).get("curated"))
if web_n:
    comm_meta[WEB_COMM] = {"label": WEB_LABEL, "short": WEB_SHORT, "videos": web_curated or web_n}
json.dump(videos, open(f"{OUT}/videos.json", "w"), separators=(",", ":"))
json.dump(mentions, open(f"{OUT}/mentions.json", "w"), separators=(",", ":"))
json.dump(related, open(f"{OUT}/related.json", "w"), separators=(",", ":"))
json.dump(authors, open(f"{OUT}/authors.json", "w"), separators=(",", ":"))
json.dump(tax, open(f"{OUT}/taxonomy.json", "w"), separators=(",", ":"))
json.dump(comm_meta, open(f"{OUT}/communities.json", "w"), separators=(",", ":"))
print("videos", len(videos), "| dropped", dropped, "| new from packs", dict(added), "| communities", dict(communities.most_common(12)),
      "| wiki-cited", sum(1 for v in videos if v.get("wiki")), "| web-linked", web_n,
      "| web-only", sum(1 for v in videos if v.get("web") and not v["comm"]), "| embeddings computed this run", embedded)
