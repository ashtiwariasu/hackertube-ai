"""Weekly HN refresh lane for hackertube.ai (contract: packs/PACKS.md).

Fetches HN comments and story texts with YouTube links posted since --since from the HN Algolia API,
backfills the full HN history of any video that is not yet in the catalogue, applies the inclusion
rule over old + new mentions, labels new mentions (claude-haiku-5-5, Batches), writes cards for newly
qualifying videos (claude-sonnet-5-5, Batches) and recomputes stats for catalogue videos that gained
mentions.

Standard library + anthropic SDK only. Run:
  ANTHROPIC_API_KEY=... YOUTUBE_API_KEY=... .venv/bin/python -I packs/hn-fresh/refresh_hn.py [--since 2026-08] [--dry-run]
Keys fall back to `gcloud secrets versions access` in $GCP_PROJECT when the env vars are unset.
All caches live in packs/hn-fresh/work/, so a re-run only fetches, labels and writes what is missing.
"""
import argparse, collections, concurrent.futures, threading, datetime as dt, html, json, os, re, subprocess, sys, time
import urllib.parse, urllib.request

PACK = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(PACK))
OLD = os.path.join(ROOT, "data", "raw", "hackertube-data")
TAXONOMY = os.path.join(ROOT, "app", "data", "taxonomy.json")
WORK = os.path.join(PACK, "work")

ALGOLIA = "https://hn.algolia.com/api/v1/"
LABEL_MODEL, CARD_MODEL = "claude-haiku-5-5", "claude-sonnet-5-5"
# Batch prices (50% of list), US$ per million tokens: input, output
PRICE = {LABEL_MODEL: (0.05, 0.25), CARD_MODEL: (1.00, 5.00)}
STANCES = ["strong_rec", "rec", "reference", "critical", "joke"]
ORDER = {s: i for i, s in enumerate(STANCES)}
RECS = {"rec", "strong_rec"}
CONTENT_TYPES = ["talk", "lecture", "course", "documentary", "explainer", "interview", "podcast", "tutorial",
                 "demo", "news", "commentary", "comedy", "music", "other"]
LEVELS = ["general", "intro", "intermediate", "advanced"]
SHELF = ["evergreen", "dated", "news"]
MAX_KEEP = 20

YT_RE = re.compile(
    r"(?:youtube(?:-nocookie)?\.com/(?:watch/?\?(?:[^#\s\"'<>]*?&(?:amp;)?)?v=|embed/|shorts/|live/|v/|e/)"
    r"|youtu\.be/)([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])", re.I)
ANCHOR_RE = re.compile(r"<a\s[^>]*?href=\"([^\"]*)\"[^>]*>.*?</a>", re.S | re.I)
URL_RE = re.compile(r"(?:https?://|\b(?:www\.|m\.)?(?:youtube\.com|youtu\.be)/)[^\s<>\"\x00]+", re.I)
PH_RE = re.compile(r"\x00(\d+)\x00")

stats_log = collections.OrderedDict()   # stage counts for REPORT.md
problems = []


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def jl_read(path):
    if not os.path.exists(path):
        return []
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:   # a line cut short by an interrupted run
                pass
    return rows


def jl_write(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


def jl_append(path, rows):
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def secret(env, name):
    if os.environ.get(env):
        return os.environ[env]
    project = os.environ.get("GCP_PROJECT", "")
    if not project:
        raise SystemExit(f"{env} not set (and no GCP_PROJECT to read the {name} secret from)")
    out = subprocess.run(["gcloud", "secrets", "versions", "access", "latest", "--secret", name,
                          "--project", project], capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        raise SystemExit(f"{env} not set and gcloud secret {name} unavailable")
    return out.stdout.strip()


# ---------------------------------------------------------------- HTTP (polite, retried)
_last_call = [0.0]
_rate_lock = threading.Lock()


def get_json(url, min_gap=0.25):
    """GET JSON; at most one request starts every min_gap seconds across all threads (<5 req/s)."""
    for attempt in range(6):
        with _rate_lock:
            wait = _last_call[0] + min_gap - time.time()
            if wait > 0:
                time.sleep(wait)
            _last_call[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "hackertube-hn-fresh/1.0"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001 (network: retry with backoff)
            log("retry", attempt, type(e).__name__, str(e)[:120])
            time.sleep(2 * (attempt + 1))
    raise SystemExit("HTTP failed: " + url.split("key=")[0])


# ---------------------------------------------------------------- text handling
def vids_in(url):
    return {m.group(1) for m in YT_RE.finditer(url or "")}


def tokenize(raw):
    """HN HTML -> (text with \\x00i\\x00 link placeholders, [urls], own-text-without-quotes)."""
    links = []

    def keep(url):
        links.append(url)
        return f" \x00{len(links) - 1}\x00 "

    s = ANCHOR_RE.sub(lambda m: keep(html.unescape(m.group(1))), raw or "")
    paras = re.split(r"<p>", s, flags=re.I)
    out, own = [], []
    for p in paras:
        p = html.unescape(re.sub(r"<[^>]+>", "", p))
        p = URL_RE.sub(lambda m: keep(m.group(0)), p)
        out.append(p)
        if not p.lstrip().startswith(">"):   # quoted parent text is not the author's own
            own.append(p)
    text = " ".join(out)
    own_text = re.sub(r"\s+", " ", PH_RE.sub(" ", " ".join(own))).strip()
    return text, links, own_text


def render(text, links, vid):
    def sub(m):
        return "[VIDEO]" if vid in vids_in(links[int(m.group(1))]) else "[LINK]"
    return re.sub(r"\s+", " ", PH_RE.sub(sub, text)).strip()[:1500]


def hits_to_mentions(hits, source):
    out = []
    for h in hits:
        raw = h.get("comment_text") or h.get("story_text") or ""
        if "youtu" not in raw.lower():
            continue
        text, links, own = tokenize(raw)
        vids = set()
        for u in links:
            vids |= vids_in(u)
        if not vids:
            continue
        ts = int(h["created_at_i"])
        cid = str(h["objectID"])
        is_story = "story_text" in h and not h.get("comment_text")
        for vid in sorted(vids):
            out.append({
                "id": cid, "url": f"https://news.ycombinator.com/item?id={cid}", "vid": vid, "community": "hn",
                "author": h.get("author") or "", "month": dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m"),
                "context": (h.get("title") if is_story else h.get("story_title")) or "",
                "text": render(text, links, vid), "_own_len": len(own), "_ts": ts, "_source": source,
            })
    return out


# ---------------------------------------------------------------- HN Algolia
def walk_by_date(query, tag, since_ts, until_ts):
    upper, seen = until_ts, {}
    while True:
        q = urllib.parse.urlencode({"tags": tag, "query": query, "hitsPerPage": 1000,
                                    "numericFilters": f"created_at_i>={since_ts},created_at_i<{upper}"})
        d = get_json(ALGOLIA + "search_by_date?" + q)
        hits = d["hits"]
        for h in hits:
            seen[h["objectID"]] = h
        # Algolia sometimes returns a short page when it times out, so trust nbHits rather than the page size
        if not hits or (len(hits) < 1000 and d.get("nbHits", 0) <= len(hits)):
            break
        if len(hits) < 1000:
            problems.append(f"Algolia returned a short page ({len(hits)} of nbHits {d.get('nbHits')}) for "
                            f"{query!r}/{tag}; kept walking (hits from earlier runs are cached and merged)")
        nxt = min(int(h["created_at_i"]) for h in hits) + 1
        if nxt >= upper:
            problems.append(f"Algolia window stuck at {upper} for {query}/{tag}")
            break
        upper = nxt
    return seen


def slim(h):
    keys = ("objectID", "author", "created_at_i", "comment_text", "story_text", "story_id", "story_title",
            "title", "parent_id", "url")
    return {k: h[k] for k in keys if h.get(k) is not None}


def fetch_recent(since_ts):
    path = os.path.join(WORK, "hn_hits.jsonl")
    cache = {h["objectID"]: h for h in jl_read(path)}
    until = int(time.time()) + 60
    got = {}
    for query in ("youtube", "youtu.be"):
        for tag in ("comment", "story"):
            res = walk_by_date(query, tag, since_ts, until)
            log(f"algolia {query!r} {tag}: {len(res)} hits")
            stats_log[f"algolia hits: query {query!r}, {tag}s"] = len(res)
            got.update(res)
    for k, h in got.items():
        cache[k] = slim(h)
    jl_write(path, sorted(cache.values(), key=lambda h: int(h["created_at_i"])))
    return cache


def backfill_history(vids):
    """Full-history HN search for each video id (one request per id, cached forever)."""
    path = os.path.join(WORK, "history.jsonl")
    done = {r["vid"]: r for r in jl_read(path)}
    todo = [v for v in sorted(vids) if v not in done]
    log(f"history backfill: {len(todo)} ids to search ({len(vids) - len(todo)} cached)")
    def search(vid):
        q = urllib.parse.urlencode({"tags": "(comment,story)", "query": vid, "hitsPerPage": 1000,
                                    "typoTolerance": "false"})
        hits = [slim(h) for h in get_json(ALGOLIA + "search?" + q)["hits"]
                if vid in (h.get("comment_text") or h.get("story_text") or "")]
        return {"vid": vid, "hits": hits, "searched_at": int(time.time())}

    with open(path, "a", encoding="utf-8") as f, concurrent.futures.ThreadPoolExecutor(4) as pool:
        for i, row in enumerate(pool.map(search, todo)):
            done[row["vid"]] = row
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush()
            if i % 250 == 0:
                log(f"  searched {i + 1}/{len(todo)}")
    return done


# ---------------------------------------------------------------- YouTube
def youtube_meta(vids, key):
    path = os.path.join(WORK, "youtube_meta.jsonl")
    cache = {r["vid"]: r for r in jl_read(path)}
    todo = [v for v in sorted(vids) if v not in cache]
    for i in range(0, len(todo), 50):
        ids = todo[i:i + 50]
        q = urllib.parse.urlencode({"part": "snippet,contentDetails,statistics,status", "id": ",".join(ids), "key": key})
        items = {it["id"]: it for it in get_json("https://www.googleapis.com/youtube/v3/videos?" + q, 0.2)["items"]}
        rows = [{"vid": v, "item": items.get(v), "fetched_at": int(time.time())} for v in ids]
        jl_append(path, rows)
        for r in rows:
            cache[r["vid"]] = r
    return cache


def playable(item):
    st = (item or {}).get("status", {})
    return bool(item) and st.get("privacyStatus") == "public" and st.get("embeddable", True)


def fmt_duration(iso):
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return "unknown"
    d, h, mi, s = (int(x or 0) for x in m.groups())
    h += 24 * d
    return f"{h}h{mi:02d}m" if h else f"{mi}m{s:02d}s"


# ---------------------------------------------------------------- inclusion rule, selection, stats
def qualifies(ms):
    return len({m["author"] for m in ms}) >= 3 and len({m["month"] for m in ms}) >= 3


def select(ms):
    """At most 20 mentions, favouring different authors, then recommendations (then month spread, recency)."""
    def key(m, months):
        return (m["stance"] not in RECS, m["month"] in months, ORDER[m["stance"]], -int(m["id"]))
    kept, months, authors = [], set(), set()
    per_author = {}
    for m in sorted(ms, key=lambda m: key(m, set())):
        per_author.setdefault(m["author"], m)
    for pool in (list(per_author.values()), [m for m in ms if m not in per_author.values()]):
        while pool and len(kept) < MAX_KEEP:
            pool.sort(key=lambda m: key(m, months))
            m = pool.pop(0)
            kept.append(m)
            months.add(m["month"])
            authors.add(m["author"])
    return kept


def compute_stats(ms):
    st = collections.Counter(m["stance"] for m in ms)
    stats = {"mentions": len(ms),
             "authors": len({m["author"] for m in ms}),
             "recommenders": len({m["author"] for m in ms if m["stance"] in RECS}),
             "rec_months": len({m["month"] for m in ms if m["stance"] in RECS}),
             "strong_recs": st["strong_rec"],
             "stances": {s: st[s] for s in STANCES},
             "first_year": min(int(m["month"][:4]) for m in ms),
             "last_year": max(int(m["month"][:4]) for m in ms)}
    score = stats["rec_months"] + 0.5 * len({m["month"] for m in ms if m["stance"] == "strong_rec"})
    return stats, score


# ---------------------------------------------------------------- Anthropic batches
def spend_path():
    return os.path.join(WORK, "spend.jsonl")


def cost_of(model, usage):
    pin, pout = PRICE[model]
    inp = (usage.get("input_tokens", 0) + 1.25 * usage.get("cache_creation_input_tokens", 0)
           + 0.1 * usage.get("cache_read_input_tokens", 0))
    return (inp * pin + usage.get("output_tokens", 0) * pout) / 1e6


def run_batches(client, kind, model, requests, on_result):
    """Submit requests {custom_id: params} (skipping ones already pending), wait, call on_result(cid, msg|None, err)."""
    from anthropic.types.messages.batch_create_params import Request
    state_path = os.path.join(WORK, "batches.json")
    state = json.load(open(state_path)) if os.path.exists(state_path) else []
    pending = [b for b in state if b["kind"] == kind and not b.get("ingested")]
    pending_ids = {c for b in pending for c in b["custom_ids"]}
    todo = {c: p for c, p in requests.items() if c not in pending_ids}
    ids = list(todo)
    for i in range(0, len(ids), 5000):
        chunk = ids[i:i + 5000]
        b = client.messages.batches.create(requests=[Request(custom_id=c, params=todo[c]) for c in chunk])
        log(f"submitted {kind} batch {b.id} ({len(chunk)} requests)")
        entry = {"id": b.id, "kind": kind, "model": model, "custom_ids": chunk, "submitted": int(time.time())}
        state.append(entry)
        pending.append(entry)
        json.dump(state, open(state_path, "w"), indent=1)
    for entry in pending:
        while True:
            b = client.messages.batches.retrieve(entry["id"])
            if b.processing_status == "ended":
                break
            rc = b.request_counts
            log(f"  {kind} batch {entry['id']}: processing={rc.processing} succeeded={rc.succeeded} errored={rc.errored}")
            time.sleep(30)
        usage_tot = collections.Counter()
        n_ok = n_err = 0
        for r in client.messages.batches.results(entry["id"]):
            if r.result.type == "succeeded":
                msg = r.result.message
                u = msg.usage
                for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
                    usage_tot[k] += getattr(u, k, 0) or 0
                n_ok += 1
                on_result(r.custom_id, msg, None)
            else:
                n_err += 1
                err = getattr(r.result, "error", None)
                on_result(r.custom_id, None, f"{r.result.type}: {str(err)[:300]}")
        cost = cost_of(entry["model"], usage_tot)
        jl_append(spend_path(), [{"batch": entry["id"], "kind": kind, "model": entry["model"], "ok": n_ok,
                                  "errored": n_err, "usage": dict(usage_tot), "usd": round(cost, 4),
                                  "at": int(time.time())}])
        log(f"{kind} batch {entry['id']}: ok={n_ok} errored={n_err} cost=${cost:.4f}")
        entry["ingested"] = True
        json.dump(state, open(state_path, "w"), indent=1)


def text_of(msg):
    return next((b.text for b in msg.content if b.type == "text"), "")


def est_tokens(s):
    return len(s) / 3.2 + 260   # chars/token (conservative) + structured-output overhead


LABEL_SYSTEM = """You label Hacker News comments that link to a YouTube video. In the comment, the video in question appears as [VIDEO]; any other link appears as [LINK]. You also get the title of the HN story the comment was posted under.

Decide what the commenter is doing with [VIDEO]:
- strong_rec: an enthusiastic recommendation ("best talk on X", "must watch", "changed how I think").
- rec: points people to it as worth watching or helpful for the topic.
- reference: cites it neutrally as a source, example or evidence, without recommending it.
- critical: criticises, debunks or disagrees with the video.
- joke: a meme, punchline, rickroll or sarcastic link.

why: at most 15 words on what a viewer gets from the video, according to this comment. Use "" for joke or critical.
quote: a short excerpt (at most 25 words) copied character for character from the comment that best shows the commenter's view of the video, without [VIDEO] or [LINK]. Use "" if nothing fits."""

LABEL_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["stance", "why", "quote"],
                "properties": {"stance": {"type": "string", "enum": STANCES},
                               "why": {"type": "string"}, "quote": {"type": "string"}}}


def label_params(m):
    return {"model": LABEL_MODEL, "max_tokens": 2048, "system": LABEL_SYSTEM,
            "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": LABEL_SCHEMA}},
            "messages": [{"role": "user", "content": f"HN story title: {m['context'] or '(none)'}\n\nComment:\n{m['text']}"}]}


def card_system(tax):
    cats = "\n".join(f"{c['id']}: {c['name']} ({a['name']})" for a in tax["areas"] for c in a["categories"])
    return f"""You write catalogue cards for HackerTube, a site of YouTube videos that Hacker News commenters keep pointing each other to. You get the video's YouTube metadata and up to 20 HN comments that link it (each with an id, month, the commenter's stance, and the comment, where [VIDEO] is this video and [LINK] is any other link).

Fields:
- summary: 2-3 plain sentences on what the video is (speaker, subject, format). Describe the video, not the comments.
- why: one sentence on why HN people point others to it (or how HN uses it), grounded in the comments.
- content_type, level, shelf_life: pick the best value. level is usually "general" unless the video clearly targets beginners or experts. shelf_life "news" is for videos that only matter around one event; "dated" for ones that age (product launches, old benchmarks); otherwise "evergreen".
- primary_topic: a short lowercase phrase. tags: 3-6 short lowercase topic tags.
- categories: 1 to 3 category ids from the list below, best first.
- quote_mention_id and quote_text: the single best pull quote about the video, copied character for character from one comment (at most 30 words, no [VIDEO] or [LINK]), with that comment's id. Use "" for both if no comment has a good quote.

Category ids:
{cats}"""


def card_schema(cat_ids):
    s = {"type": "string"}
    return {"type": "object", "additionalProperties": False,
            "required": ["summary", "why", "content_type", "level", "shelf_life", "primary_topic", "tags",
                         "categories", "quote_mention_id", "quote_text"],
            "properties": {"summary": s, "why": s, "content_type": {"type": "string", "enum": CONTENT_TYPES},
                           "level": {"type": "string", "enum": LEVELS}, "shelf_life": {"type": "string", "enum": SHELF},
                           "primary_topic": s, "tags": {"type": "array", "items": s},
                           "categories": {"type": "array", "items": {"type": "string", "enum": cat_ids}},
                           "quote_mention_id": s, "quote_text": s}}


def card_params(item, ms, system, schema):
    sn, cd = item.get("snippet", {}), item.get("contentDetails", {})
    lines = [f"Title: {sn.get('title', '')}", f"Channel: {sn.get('channelTitle', '')}",
             f"Published: {(sn.get('publishedAt') or '')[:10]}", f"Duration: {fmt_duration(cd.get('duration'))}",
             f"YouTube description (first 1,500 chars):\n{(sn.get('description') or '')[:1500]}", "", "HN comments:"]
    for m in ms:
        lines.append(f"[id {m['id']} | {m['month']} | {m['stance']}] story: {m['context']}\n{m['text'][:900]}")
    return {"model": CARD_MODEL, "max_tokens": 6000, "system": system,
            "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": schema}},
            "messages": [{"role": "user", "content": "\n".join(lines)}]}


# ---------------------------------------------------------------- main
def month_start_ts(s):
    y, m = int(s[:4]), int(s[5:7])
    d = int(s[8:10]) if len(s) >= 10 else 1
    return int(dt.datetime(y, m, d, tzinfo=dt.timezone.utc).timestamp())


def public(m):
    return {k: v for k, v in m.items() if not k.startswith("_")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", help="YYYY-MM or YYYY-MM-DD (default: start of the latest month already covered)")
    ap.add_argument("--budget", type=float, default=10.0, help="US$ cap for this run's Anthropic batches")
    ap.add_argument("--dry-run", action="store_true", help="fetch and count, print the LLM estimate, submit nothing")
    args = ap.parse_args()
    os.makedirs(WORK, exist_ok=True)
    run_started = int(time.time())

    # ---- old dataset
    old_videos = {v["vid"]: v for v in jl_read(os.path.join(OLD, "videos.jsonl"))}
    old_ms = collections.defaultdict(list)
    for m in jl_read(os.path.join(OLD, "mentions.jsonl")):
        old_ms[m["vid"]].append({"id": str(m["hn_comment_id"]), "url": m["hn_url"], "vid": m["vid"], "community": "hn",
                                 "author": m["author"], "month": m["month"], "context": m["story_title"],
                                 "stance": m["stance"], "why": m["why"], "quote": m["quote"], "text": m["text"]})
    old_keys = {(m["id"], m["vid"]) for ms in old_ms.values() for m in ms}
    old_max_month = max(m["month"] for ms in old_ms.values() for m in ms)
    stats_log["old catalogue videos"] = len(old_videos)
    stats_log["old mentions"] = len(old_keys)

    prev = jl_read(os.path.join(PACK, "mentions.jsonl"))
    since = args.since or max([old_max_month] + [m["month"] for m in prev])
    since_ts = month_start_ts(since)
    log(f"since {since} ({since_ts}); old data ends {old_max_month}")

    # ---- 1. fetch recent comments + story texts
    hits = fetch_recent(since_ts)
    recent = hits_to_mentions(hits.values(), "recent")
    stats_log["cached HN items with a YouTube-ish word (all runs)"] = len(hits)
    stats_log["(item, video) pairs with a YouTube video link"] = len(recent)
    stats_log["distinct items with a video link"] = len({m["id"] for m in recent})

    # ---- 2. own-text rule, dedupe vs old
    stats_log["old-data mentions in window rejected by this run's own-text rule (quotes not counted)"] = sum(
        (m["id"], m["vid"]) in old_keys and m["_own_len"] < 100 for m in recent)
    recent = [m for m in recent if m["_own_len"] >= 100]
    stats_log["pairs with >=100 chars own text"] = len(recent)
    overlap = sum((m["id"], m["vid"]) in old_keys for m in recent)
    new_by_key = {(m["id"], m["vid"]): m for m in recent if (m["id"], m["vid"]) not in old_keys}
    stats_log["already in old mentions.jsonl (overlap)"] = overlap
    stats_log["new mentions (recent window)"] = len(new_by_key)
    new_vids = {v for _, v in new_by_key}
    upd_vids = new_vids & set(old_videos)
    cand_vids = new_vids - set(old_videos)
    stats_log["videos with new mentions"] = len(new_vids)
    stats_log["  of which already in catalogue"] = len(upd_vids)
    stats_log["  of which not in catalogue (candidates)"] = len(cand_vids)

    # ---- 3. history backfill for candidates (old mentions.jsonl only covers catalogue videos)
    hist = backfill_history(cand_vids)
    hist_added = 0
    for vid in cand_vids:
        for m in hits_to_mentions(hist[vid]["hits"], "history"):
            k = (m["id"], m["vid"])
            if m["vid"] == vid and m["_own_len"] >= 100 and k not in old_keys and k not in new_by_key:
                new_by_key[k] = m
                hist_added += 1
    stats_log["historical mentions backfilled for candidates (>=100 chars)"] = hist_added
    by_vid = collections.defaultdict(list)
    for m in new_by_key.values():
        by_vid[m["vid"]].append(m)
    qual = {v for v in cand_vids if qualifies(by_vid[v])}
    stats_log["candidates meeting the inclusion rule (3 authors, 3 months)"] = len(qual)

    # ---- 4. YouTube availability for qualifying candidates
    yt = youtube_meta(qual, secret("YOUTUBE_API_KEY", "hackertube-youtube-api-key")) if qual else {}
    dead = {v for v in qual if not playable(yt[v]["item"])}
    qual -= dead
    stats_log["candidates dropped: deleted/private/not embeddable"] = len(dead)
    stats_log["new qualifying videos"] = len(qual)

    # ---- 5. label new mentions of catalogue + qualifying videos
    to_label = [m for m in new_by_key.values() if m["vid"] in upd_vids or m["vid"] in qual]
    stats_log["new mentions needing labels (catalogue + qualifying videos)"] = len(to_label)
    lab_path = os.path.join(WORK, "labels.jsonl")
    labels = {r["key"]: r for r in jl_read(lab_path)}
    todo = {f"{m['id']}_{m['vid']}": m for m in to_label if f"{m['id']}_{m['vid']}" not in labels}
    tax = json.load(open(TAXONOMY, encoding="utf-8"))
    cat_ids = [c["id"] for a in tax["areas"] for c in a["categories"]]
    cat_meta = {c["id"]: (c["name"], a["id"], a["name"]) for a in tax["areas"] for c in a["categories"]}
    system, schema = card_system(tax), card_schema(cat_ids)

    lab_in = sum(est_tokens(LABEL_SYSTEM + label_params(m)["messages"][0]["content"]) for m in todo.values())
    lab_cost = (lab_in * PRICE[LABEL_MODEL][0] + len(todo) * 400 * PRICE[LABEL_MODEL][1]) / 1e6
    # card estimate uses up to 20 mentions per video before labels exist (worst case on input size)
    cached_cards = {r["vid"] for r in jl_read(os.path.join(WORK, "cards.jsonl"))}
    need_cards = [v for v in qual if v not in cached_cards]
    card_in = sum(est_tokens(system) + sum(min(len(m["text"]), 900) + 80 for m in by_vid[v][:20]) / 3.2 + 600
                  for v in need_cards)
    card_cost = (card_in * PRICE[CARD_MODEL][0] + len(need_cards) * 1500 * PRICE[CARD_MODEL][1]) / 1e6
    worst = card_cost + (len(need_cards) * 4500 * PRICE[CARD_MODEL][1]) / 1e6 + lab_cost
    log(f"estimate: labels {len(todo)} reqs ${lab_cost:.3f}; cards {len(need_cards)} reqs ${card_cost:.3f}; "
        f"pessimistic total ${worst:.2f}; budget ${args.budget:.2f}")
    stats_log["estimate before submit, uncached work this run (US$): labels"] = round(lab_cost, 3)
    stats_log["estimate before submit, uncached work this run (US$): cards"] = round(card_cost, 3)
    if args.dry_run:
        log("dry run: stopping before any batch")
        print(json.dumps(stats_log, indent=1))
        return
    if todo or need_cards:
        jl_append(os.path.join(WORK, "estimates.jsonl"), [{"at": int(time.time()), "labels": len(todo),
                  "labels_usd": round(lab_cost, 3), "cards": len(need_cards), "cards_usd": round(card_cost, 3)}])
    if lab_cost + card_cost > args.budget:
        problems.append(f"estimate ${lab_cost + card_cost:.2f} exceeds budget ${args.budget:.2f}; nothing submitted")
        write_report(run_started, since, [], [], [], args.budget)
        raise SystemExit(problems[-1])

    import anthropic
    client = anthropic.Anthropic(api_key=secret("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY"))

    def on_label(cid, msg, err):
        if err or msg.stop_reason not in ("end_turn", "stop_sequence"):
            problems.append(f"label {cid}: {err or msg.stop_reason}")
            return
        try:
            d = json.loads(text_of(msg))
        except json.JSONDecodeError:
            problems.append(f"label {cid}: unparseable output")
            return
        row = {"key": cid, "stance": d["stance"], "why": d["why"].strip(), "quote": d["quote"].strip(),
               "model": LABEL_MODEL}
        labels[cid] = row
        jl_append(lab_path, [row])

    if todo:
        run_batches(client, "label", LABEL_MODEL, {c: label_params(m) for c, m in todo.items()}, on_label)

    labelled = []
    quote_blanked = 0
    for m in to_label:
        lab = labels.get(f"{m['id']}_{m['vid']}")
        if not lab:
            continue
        m = dict(m, stance=lab["stance"], why=lab["why"], quote=lab["quote"])
        if m["stance"] in ("joke", "critical"):
            m["why"] = ""
        m["why"] = " ".join(m["why"].split()[:15])
        if m["quote"] and (m["quote"] not in m["text"] or "[VIDEO]" in m["quote"] or "[LINK]" in m["quote"]):
            m["quote"] = ""
            quote_blanked += 1
        labelled.append(m)
    stats_log["new mentions labelled"] = len(labelled)
    stats_log["label quotes blanked (not verbatim)"] = quote_blanked

    lab_by_vid = collections.defaultdict(list)
    for m in labelled:
        lab_by_vid[m["vid"]].append(m)

    # ---- 6. updated stats for catalogue videos that gained mentions
    updated = []
    for vid in sorted(upd_vids):
        if not lab_by_vid[vid]:
            continue
        kept = select(old_ms[vid] + lab_by_vid[vid])
        stats, score = compute_stats(kept)
        updated.append({"vid": vid, "new_mentions": len(lab_by_vid[vid]), "stats": stats, "score": score,
                        "old_score": old_videos[vid]["score"], "kept_ids": [m["id"] for m in kept]})

    for u in updated:
        if u["score"] < u["old_score"]:
            problems.append(f"{u['vid']}: score fell {u['old_score']} -> {u['score']} because the 20-mention cap "
                            "now favours a new author over an older recommending mention")
    # ---- 7. cards for new qualifying videos
    kept_new = {v: select(lab_by_vid[v]) for v in qual if lab_by_vid[v] and qualifies(lab_by_vid[v])}
    card_path = os.path.join(WORK, "cards.jsonl")
    cards = {r["vid"]: r for r in jl_read(card_path)}
    todo_cards = {f"card_{v}": card_params(yt[v]["item"], kept_new[v], system, schema)
                  for v in kept_new if v not in cards}
    spent_so_far = sum(r["usd"] for r in jl_read(spend_path()) if r["at"] >= run_started)
    card_cost = sum(est_tokens(p["system"] + p["messages"][0]["content"]) for p in todo_cards.values()) \
        * PRICE[CARD_MODEL][0] / 1e6 + len(todo_cards) * 1500 * PRICE[CARD_MODEL][1] / 1e6
    log(f"card estimate now ${card_cost:.3f} for {len(todo_cards)} cards; spent this run ${spent_so_far:.3f}")
    if spent_so_far + card_cost > args.budget:
        problems.append(f"card estimate ${card_cost:.2f} + spent ${spent_so_far:.2f} exceeds budget; cards skipped")
        todo_cards = {}

    def on_card(cid, msg, err):
        vid = cid[5:]
        if err or msg.stop_reason not in ("end_turn", "stop_sequence"):
            problems.append(f"card {vid}: {err or msg.stop_reason}")
            return
        try:
            d = json.loads(text_of(msg))
        except json.JSONDecodeError:
            problems.append(f"card {vid}: unparseable output")
            return
        d["vid"] = vid
        cards[vid] = d
        jl_append(card_path, [d])

    if todo_cards:
        run_batches(client, "card", CARD_MODEL, todo_cards, on_card)

    videos, out_mentions, card_quote_blanked = [], [], 0
    for vid, kept in sorted(kept_new.items()):
        c = cards.get(vid)
        if not c:
            continue
        sn = yt[vid]["item"]["snippet"]
        stats, score = compute_stats(kept)
        cats = [x for x in dict.fromkeys(c["categories"]) if x in cat_meta][:3]
        quote = None
        qm = next((m for m in kept if m["id"] == c["quote_mention_id"].strip()), None)
        qt = c["quote_text"].strip()
        if qm and qt and qt in qm["text"] and "[VIDEO]" not in qt and "[LINK]" not in qt:
            quote = {"text": qt, "mention_id": qm["id"], "year": qm["month"][:4]}
        elif qt:
            card_quote_blanked += 1
            alt = next((m for m in kept if m["stance"] in RECS and m["quote"]), None)
            if alt:
                quote = {"text": alt["quote"], "mention_id": alt["id"], "year": alt["month"][:4]}
        videos.append({"vid": vid, "community": "hn", "title": sn.get("title", ""), "channel": sn.get("channelTitle", ""),
                       "summary": c["summary"], "why": c["why"], "content_type": c["content_type"], "level": c["level"],
                       "shelf_life": c["shelf_life"], "primary_topic": c["primary_topic"], "tags": c["tags"][:8],
                       "categories": cats, "quote": quote, "stats": stats, "score": score})
        out_mentions += kept
    videos.sort(key=lambda v: -v["score"])
    norm = lambda t: re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()
    old_titles = {norm(v["title"]): v["vid"] for v in old_videos.values()}
    for v in videos:
        if norm(v["title"]) in old_titles:
            problems.append(f"possible duplicate upload: new {v['vid']} has the same title as catalogue "
                            f"{old_titles[norm(v['title'])]} ({v['title']})")
    stats_log["cards written (new videos.jsonl rows)"] = len(videos)
    stats_log["card quotes not verbatim (replaced or nulled)"] = card_quote_blanked
    for m in labelled:
        if m["vid"] in upd_vids:
            out_mentions.append(m)
    out_mentions.sort(key=lambda m: (m["vid"], m["month"], m["id"]))
    stats_log["mentions.jsonl rows"] = len(out_mentions)
    stats_log["  for new videos (kept, <=20 each)"] = sum(m["vid"] in kept_new for m in out_mentions)
    stats_log["  for catalogue videos (all new)"] = sum(m["vid"] in upd_vids for m in out_mentions)
    stats_log["catalogue videos updated (updated_stats.jsonl rows)"] = len(updated)

    field_order = ["id", "url", "vid", "community", "author", "month", "context", "stance", "why", "quote", "text"]
    jl_write(os.path.join(PACK, "mentions.jsonl"), [{k: m[k] for k in field_order} for m in out_mentions])
    jl_write(os.path.join(PACK, "videos.jsonl"), videos)
    jl_write(os.path.join(PACK, "updated_stats.jsonl"), updated)
    write_report(run_started, since, videos, out_mentions, updated, args.budget)
    log("done")
    print(json.dumps(stats_log, indent=1))


def write_report(run_started, since, videos, mentions, updated, budget):
    spend = jl_read(spend_path())
    this_run = [r for r in spend if r["at"] >= run_started]

    def tot(rows):
        u = collections.Counter()
        for r in rows:
            u.update(r["usage"])
        return u, sum(r["usd"] for r in rows)

    first = min((h["created_at_i"] for h in jl_read(os.path.join(WORK, "hn_hits.jsonl"))), default=None)
    first = dt.datetime.fromtimestamp(int(first), dt.timezone.utc).strftime("%Y-%m-%d") if first else since
    now = dt.datetime.now(dt.timezone.utc)
    lines = [f"# hn-fresh: HN mentions {first} to {now.strftime('%Y-%m-%d')}", "",
             f"Run finished {now.strftime('%Y-%m-%d %H:%M UTC')} by `refresh_hn.py`; this run re-fetched from "
             f"{since}. Counts cover everything cached since the first run (Algolia hit counts are this run's).",
             "", "## Counts at each stage", "", "| stage | count |", "|---|---|"]
    lines += [f"| {k} | {v} |" for k, v in stats_log.items()]
    top = sorted(updated, key=lambda u: -(u["score"] - u["old_score"]))[:10]
    if videos:
        lines += ["", "## Top new videos", ""]
        lines += [f"- {v['title']} ({v['channel']}), score {v['score']}, {v['stats']['authors']} authors"
                  for v in videos[:15]]
    if top:
        lines += ["", "## Biggest score gains among catalogue videos", ""]
        lines += [f"- {u['vid']}: {u['old_score']} -> {u['score']} (+{u['new_mentions']} mentions)" for u in top]
    lines += ["", "## Models, tokens, spend", "",
              f"- Stance labels: `{LABEL_MODEL}`, Message Batches, structured output {{stance, why, quote}}, effort low.",
              f"- Cards: `{CARD_MODEL}`, Message Batches, structured output (card fields + 1-3 taxonomy ids), effort low.",
              "", "| scope | batch | model | ok | errored | input tok | output tok | US$ |", "|---|---|---|---|---|---|---|---|"]
    for r in spend:
        scope = "this run" if r["at"] >= run_started else "earlier run"
        lines.append(f"| {scope} | {r['batch']} | {r['model']} | {r['ok']} | {r['errored']} | "
                     f"{r['usage'].get('input_tokens', 0)} | {r['usage'].get('output_tokens', 0)} | {r['usd']:.4f} |")
    u, usd = tot(this_run)
    ua, usda = tot(spend)
    lines += ["", f"**Total spent, all runs: US${usda:.4f}** ({ua.get('input_tokens', 0):,} input + "
              f"{ua.get('output_tokens', 0):,} output tokens). This run: US${usd:.4f} (budget per run US${budget:.2f}; "
              "a re-run reuses cached labels and cards, so it only pays for new mentions and videos).",
              *[f"Estimate before submitting ({dt.datetime.fromtimestamp(e['at'], dt.timezone.utc):%Y-%m-%d %H:%M}): "
                f"US${e['labels_usd']} for {e['labels']} labels + US${e['cards_usd']} for {e['cards']} cards."
                for e in jl_read(os.path.join(WORK, "estimates.jsonl"))],
              "Prices used: batch rates (50% of list), Haiku 5.5 $0.05/$0.25 and Sonnet 5.5 $1/$5 per M input/output tokens.",
              "", "## Method notes", "",
              "- Source: HN Algolia `search_by_date`, queries `youtube` and `youtu.be`, tags `comment` and `story` "
              "(story_text only), walked backwards in time windows of <=1,000 hits, at most 4 requests/s.",
              "- Video ids from watch?v=, youtu.be/, /embed/, /shorts/, /live/, /v/ links (including youtube-nocookie).",
              "- Own text: HTML stripped, links removed, and quoted lines (starting with `>`) not counted; >=100 chars.",
              "- The old mentions.jsonl only holds mentions of the 6,454 catalogue videos, so for every other video "
              "seen in the new window the script searched HN's full history for its id (Algolia `search`, one request "
              "per id) and counted those older comments as mentions too (they were labelled like the new ones).",
              "- Overlap with the old data (it runs into 2026-08) is removed by (comment id, video id).",
              "- Inclusion rule over old + new + backfilled mentions; at most 20 kept per video, one per author first, "
              "recommendations first, then new months, then newest. Stats and score use the kept set, exactly as the "
              "open dataset does (formulas checked against all 6,454 old rows).",
              "- mentions.jsonl holds every new labelled mention of an old catalogue video, plus the kept (<=20) "
              "mentions of each new video. Mentions of videos that still do not qualify are not labelled or written.",
              "- updated_stats.jsonl adds `old_score` and `kept_ids` (the ids in the 20-mention kept set) beyond the "
              "requested fields, so the merge can pick which mentions to show.",
              "- Quotes: label quotes are kept only if they occur verbatim in the mention text; card quotes must occur "
              "verbatim in the named mention, else the first recommending mention's verified quote is used, else null.",
              "", "## Problems", ""]
    lines += [f"- {p}" for p in problems[:50]] or ["- none"]
    if len(problems) > 50:
        lines.append(f"- ... and {len(problems) - 50} more")
    lines += ["", "## Licence notes", "",
              "- HN comments: retrieved from the public HN Algolia API; comments remain the work of their authors "
              "(Hacker News data by Y Combinator). The base dataset is HackerTube open data, ODC-By 1.0, which credits "
              "the open-index/hacker-news archive.",
              "- YouTube titles and channel names come from the YouTube Data API and belong to their creators; "
              "descriptions were only sent to the model and are not stored in the outputs (the raw API cache in "
              "`work/` is private and must not be redistributed; refresh it periodically per YouTube's policies).",
              "- Stances, whys and cards are LLM-written: expect a few percent of noise."]
    with open(os.path.join(PACK, "REPORT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
