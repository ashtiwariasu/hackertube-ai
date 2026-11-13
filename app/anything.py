"""Paste anything about you: detect what each pasted thing is, read it with the right reader,
and fold everything into one taste profile with a reason attached to every pick."""
import asyncio, logging, os, re
from urllib.parse import parse_qs, urlparse

import httpx
import numpy as np

from . import aiprofile as aip
from . import screenshot as shot
from . import sources as src
from .catalog import norm_key

log = logging.getLogger("hackertube.anything")
YT_KEY = os.environ.get("YOUTUBE_API_KEY", "")
URL_RE = re.compile(r"https?://[^\s<>\"']+|(?:www\.)?(?:youtube\.com|youtu\.be|github\.com|news\.ycombinator\.com|bsky\.app|stackoverflow\.com)/[^\s<>\"']+", re.I)
VID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
VIEWS_RE = re.compile(r"\b\d[\d,.]*[KMB]?\s+views?\b", re.I)
COUNT_RE = re.compile(r"^\d[\d,.]*[KMB]?$")
DUR_RE = re.compile(r"^\d{1,2}:\d{2}(?::\d{2})?$")


class AnythingError(Exception):
    """Safe to show."""


# ---------------- detection ----------------
def classify_url(u):
    if "://" not in u:
        u = "https://" + u
    p = urlparse(u)
    host = (p.hostname or "").lower().replace("www.", "").replace("m.", "")
    path = p.path.rstrip("/")
    if host in ("youtube.com", "youtube-nocookie.com", "youtu.be"):
        q = parse_qs(p.query)
        if host == "youtu.be" and VID_RE.match(path.lstrip("/")):
            return ("yt_video", path.lstrip("/"))
        if q.get("v") and VID_RE.match(q["v"][0]):
            return ("yt_video", q["v"][0])
        if path.startswith(("/shorts/", "/embed/", "/live/")) and VID_RE.match(path.split("/")[2] if len(path.split("/")) > 2 else ""):
            return ("yt_video", path.split("/")[2])
        if q.get("list"):
            return ("yt_playlist", q["list"][0])
        if path.startswith("/@"):
            return ("yt_channel", path.split("/")[1])
        if path.startswith("/channel/"):
            return ("yt_channel_id", path.split("/")[2])
        if path.startswith(("/c/", "/user/")):
            return ("yt_channel", "@" + path.split("/")[2])
        return ("yt_other", u)
    if host == "github.com":
        parts = [x for x in path.split("/") if x]
        return ("github", parts[0]) if parts else ("website", u)
    if host == "news.ycombinator.com":
        q = parse_qs(p.query)
        return ("hn", q["id"][0]) if q.get("id") else ("website", u)
    if host == "bsky.app" and path.startswith("/profile/"):
        return ("bluesky", path.split("/")[2])
    if host.endswith("stackoverflow.com") and "/users/" in path:
        return ("stackoverflow", u)
    return ("website", u)


def looks_like_yt_page(text):
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    return len(lines) >= 12 and sum(1 for l in lines if VIEWS_RE.search(l) or DUR_RE.match(l) or COUNT_RE.match(l)) >= 4


def parse_yt_page(text):
    """Titles from a copy-pasted YouTube history, subscriptions or playlist page.
    Rows look like: [duration] title / channel [/ and other channel] / view count, or title / '2.7M views' for Shorts."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    skip = re.compile(r"^(Watched|Skip navigation|Search|Home|Shorts|Subscriptions|History|Library|You|Watch later|Liked videos|Playlists|Explore|More from YouTube|Settings|Report history|Send feedback|Clear all watch history|Pause watch history|Manage all history|Comments|Posts|Live chat|All|Videos|Podcasts|Music|Sign in|Verified|Create|Yesterday|Today|Older|Watch history|Search watch history|Sunday|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|[A-Z][a-z]{2} \d{1,2}(, \d{4})?|\d+ (days?|weeks?|months?|years?) ago)$", re.I)

    def is_count(l):
        return bool(VIEWS_RE.search(l) or COUNT_RE.match(l))

    def noise(l):
        return bool(DUR_RE.match(l) or is_count(l) or skip.match(l) or len(l) < 4 or l.startswith(("http", "•", "and ")))

    out, i, n = [], 0, len(lines)
    while i < n:
        l = lines[i]
        if noise(l):
            i += 1
            continue
        j, channel = i + 1, ""
        if j < n and not noise(lines[j]):
            channel = re.split(r"\s+[·•]\s+|\s{2,}", lines[j])[0][:80]
            j += 1
        while j < n and (lines[j].startswith("and ") or lines[j] == "•"):
            j += 1
        if j < n and (is_count(lines[j]) or DUR_RE.match(lines[j])):
            out.append({"title": l[:200], "channel": channel})
            i = j + 1 if is_count(lines[j]) else j
            continue
        if channel and j >= n:  # last row of the page, no count after it
            out.append({"title": l[:200], "channel": channel})
            break
        i += 1
    seen, uniq = set(), []
    for v in out:
        k = norm_key(v["title"])
        if k and k not in seen:
            seen.add(k)
            uniq.append(v)
    return uniq[:300]


def detect(text):
    """Return a list of (kind, value) items found in free text, plus the leftover text."""
    items, rest = [], text or ""
    for m in URL_RE.finditer(text or ""):
        kind, val = classify_url(m.group(0).rstrip(".,);"))
        if kind != "yt_other":
            items.append((kind, val))
    rest = URL_RE.sub(" ", rest).strip()
    if rest:
        if looks_like_yt_page(rest):
            items.append(("yt_page", rest))
        elif len(rest) >= 200 or rest.count("\n") >= 3:
            items.append(("profile_text", rest))
        elif re.fullmatch(r"@?[A-Za-z0-9][A-Za-z0-9_.-]{1,38}", rest):
            items.append(("handle", rest.lstrip("@")))
        else:
            items.append(("sentence", rest))
    return items


# ---------------- readers ----------------
async def yt_api(client, path, params):
    if not YT_KEY:
        raise AnythingError("YouTube link reading is not configured yet.")
    r = await client.get(f"https://www.googleapis.com/youtube/v3/{path}", params={**params, "key": YT_KEY})
    if r.status_code != 200:
        raise AnythingError("YouTube didn't answer for that link.")
    return r.json()


async def read_yt_video(client, cat, vid):
    if vid in cat.by_vid:
        v = cat.by_vid[vid]
        return {"label": v["title"][:50], "vids": [vid], "texts": [], "kind": "video"}
    d = await yt_api(client, "videos", {"part": "snippet", "id": vid})
    it = (d.get("items") or [{}])[0].get("snippet", {})
    if not it:
        raise AnythingError("That video doesn't exist or is private.")
    text = f"{it.get('title', '')} {it.get('channelTitle', '')}. {(it.get('description') or '')[:300]}"
    return {"label": it.get("title", "video")[:50], "vids": [], "texts": [text], "kind": "video",
            "channel_title": it.get("channelTitle", "")}


async def read_yt_channel(client, cat, handle=None, channel_id=None):
    params = {"part": "snippet,contentDetails"} | ({"forHandle": handle} if handle else {"id": channel_id})
    d = await yt_api(client, "channels", params)
    it = (d.get("items") or [None])[0]
    if not it:
        raise AnythingError("Couldn't find that YouTube channel.")
    uploads = it["contentDetails"]["relatedPlaylists"]["uploads"]
    name = it["snippet"]["title"]
    pl = await yt_api(client, "playlistItems", {"part": "snippet", "playlistId": uploads, "maxResults": 50})
    titles = [x["snippet"]["title"] for x in pl.get("items", [])]
    in_cat = [v["vid"] for v in cat.videos if norm_key(v["channel"]) == norm_key(name)]
    in_cat.sort(key=lambda x: -cat.by_vid[x]["score"])
    return {"label": f"{name}, {len(titles)} uploads", "vids": in_cat[:8], "texts": [f"{name}: {t}" for t in titles],
            "kind": "channel", "channel": name}


async def read_yt_playlist(client, cat, pid):
    pl = await yt_api(client, "playlistItems", {"part": "snippet", "playlistId": pid, "maxResults": 50})
    items = pl.get("items", [])
    if not items:
        raise AnythingError("That playlist is empty or private.")
    titles = [x["snippet"]["title"] for x in items]
    vids = [x["snippet"].get("resourceId", {}).get("videoId") for x in items]
    in_cat = [v for v in vids if v in cat.by_vid]
    return {"label": f"Playlist, {len(titles)} videos", "vids": in_cat, "texts": titles, "kind": "playlist"}


async def read_item(client, cat, kind, value, prior_vids=()):
    if kind == "yt_video":
        return await read_yt_video(client, cat, value)
    if kind == "yt_channel":
        return await read_yt_channel(client, cat, handle=value)
    if kind == "yt_channel_id":
        return await read_yt_channel(client, cat, channel_id=value)
    if kind == "yt_playlist":
        return await read_yt_playlist(client, cat, value)
    if kind == "github":
        r = await src.github(client, value)
        return {"label": f"GitHub @{r['handle']}, {r['label']}", "vids": [], "texts": r["texts"], "kind": "github"}
    if kind == "hn":
        r = await src.hackernews(client, value, cat)
        return {"label": f"HN {r['handle']}, {r['label']}", "vids": r.get("catalog_vids", []), "texts": r["texts"], "kind": "hn"}
    if kind == "bluesky":
        r = await src.bluesky(client, value)
        return {"label": f"Bluesky {r['handle']}, {r['label']}", "vids": [], "texts": r["texts"], "kind": "bluesky"}
    if kind == "stackoverflow":
        r = await src.stackoverflow(client, value)
        return {"label": f"Stack Overflow, {r['label']}", "vids": [], "texts": r["texts"], "kind": "stackoverflow"}
    if kind == "website":
        r = await src.website(client, value)
        return {"label": r["label"][:50], "vids": [], "texts": r["texts"], "kind": "website"}
    if kind == "yt_page":
        parsed = parse_yt_page(value)
        if len(parsed) < 3:
            raise AnythingError("That looks like a YouTube page but we couldn't find titles in it.")
        matched, _ = cat.match_titles(parsed)
        return {"label": f"YouTube page, {len(parsed)} videos read", "vids": matched, "watched": matched,
                "texts": [f"{p['title']} {p['channel']}".strip() for p in parsed], "kind": "yt_page"}
    if kind == "profile_text":
        prof = await asyncio.to_thread(aip.parse, value)
        res = await asyncio.to_thread(aip.analyse, cat, prof)
        return {"label": f"About you, {len(prof['topics'])} topics", "vids": res["vids"], "texts": res["texts"], "kind": "profile",
                "profile": prof}
    if kind == "sentence":
        return {"label": f"“{value[:40]}”", "vids": [], "texts": [value], "kind": "sentence"}
    if kind == "handle":
        async def tryit(fn, *a):
            try:
                return await fn(client, *a)
            except Exception:  # noqa: BLE001
                return None
        gh, hn, bs = await asyncio.gather(tryit(src.github, value), tryit(src.hackernews, value, cat),
                                          tryit(src.bluesky, value + ".bsky.social"))
        found = [x for x in (gh, hn, bs) if x]
        if not found:
            raise AnythingError(f"No GitHub, Hacker News or Bluesky account called {value}. Try a link instead.")
        texts, vids, labels = [], [], []
        for r in found:
            texts += r["texts"]
            vids += r.get("catalog_vids", [])
            labels.append(r["label"])
        return {"label": f"@{value}: {'; '.join(labels)}"[:80], "vids": vids, "texts": texts, "kind": "handle"}
    raise AnythingError("Couldn't tell what that was.")


async def read_images(cat, images):
    parts = await asyncio.gather(*(asyncio.to_thread(shot.extract, [raw]) for raw in images))
    ex = shot.merge_extractions(parts)
    matched, _ = cat.match_titles(ex["videos"])
    texts = [f"{v['title']} {v['channel']}".strip() for v in ex["videos"]] + [f"YouTube channel: {c}" for c in ex["channels"]]
    return {"label": f"Screenshot{'s' if len(images) > 1 else ''}, {len(ex['videos'])} videos read", "vids": matched,
            "watched": matched, "texts": texts, "kind": "screenshot"}


# ---------------- combine ----------------
KIND_WEIGHT = {"screenshot": 1.2, "yt_page": 1.2, "channel": 1.0, "playlist": 1.0, "video": 1.0, "github": 1.0, "hn": 1.0,
               "bluesky": 0.8, "stackoverflow": 1.0, "website": 1.0, "profile": 1.2, "sentence": 1.4, "handle": 1.0}


def analyse(cat, items):
    """items: list of reader results. Returns interests, picks with reasons, essentials, store."""
    anchors, watched, texts, chips, neg = [], set(), [], [], []
    for it in items:
        for v in it.get("vids", []):
            if v in cat.by_vid and v not in anchors:
                anchors.append(v)
        watched.update(v for v in it.get("watched", []) if v in cat.by_vid)
        neg += [v for v in it.get("neg", []) if v in cat.by_vid]
        for t in it.get("texts", [])[:150]:
            texts.append((t, KIND_WEIGHT.get(it.get("kind"), 1.0), it.get("label", "")))
        chips.append({"label": it.get("label", ""), "kind": it.get("kind", "")})
    if not anchors and not texts:
        return None
    parts = []
    tvecs = None
    if texts:
        tvecs = cat.embed([t for t, _, _ in texts[:300]])
        w = np.array([wt for _, wt, _ in texts[:300]], dtype=np.float32)
        m = (tvecs * w[:, None]).sum(axis=0) / w.sum()
        parts.append((1.0, (m / (np.linalg.norm(m) + 1e-9)).tolist()))
    parts.append((1.6, cat.vids_vector(anchors[-200:])))
    vec = cat.combine(parts)
    negv = cat.vids_vector(neg[-200:]) if neg else None
    if negv is not None:
        vv = np.asarray(vec, dtype=np.float32) - 0.35 * np.asarray(negv, dtype=np.float32)
        vec = [round(float(x), 5) for x in vv / (np.linalg.norm(vv) + 1e-9)]
    v = np.asarray(vec, dtype=np.float32)
    top10 = float(np.sort(cat.emb @ v)[-10:].mean())
    confidence = "strong" if (len(anchors) >= 2 or top10 >= 0.76) else ("ok" if (anchors or top10 >= 0.73) else "weak")
    pool = cat.personal(vec, exclude=set(anchors) | watched | set(neg), n=40)
    if confidence == "weak":
        pool = pool[:6] + [x for x in cat.daily_mix(cat.ranked[:120], 10, "any") if x not in pool][:6]
    picks = pool[:12]
    # reasons
    anchor_rows = [cat.by_vid[a]["i"] for a in anchors[-200:]]
    reasons = {}
    for p in picks:
        pe = cat.emb[p["i"]]
        best, reason = 0.0, ""
        if anchor_rows:
            sims = cat.emb[anchor_rows] @ pe
            j = int(np.argmax(sims))
            if sims[j] > best:
                a = cat.by_vid[anchors[-200:][j]]
                verb = "watched" if a["vid"] in watched else "like"
                best, reason = float(sims[j]), f"Because you {verb} “{a['title'][:44]}”"
        if tvecs is not None:
            sims = tvecs @ pe
            j = int(np.argmax(sims))
            if sims[j] > best + 0.03:
                t, _, label = texts[j]
                snippet = re.sub(r"^(YouTube channel|Creator|Talk or video|Stack Overflow):\s*", "", t)[:40].strip()
                best, reason = float(sims[j]), f"Matches “{snippet}”" if snippet else f"From {label}"
        if not reason or best < 0.55:
            ly = p["hn"].get("last_year")
            reason = f"Still recommended in {ly}" if ly else ""
        reasons[p["vid"]] = reason
    interests = cat.interests(vec)
    return {"chips": chips, "anchors": [cat.by_vid[a] for a in anchors[-12:]], "interests": interests, "picks": picks,
            "reasons": reasons, "confidence": confidence, "closeness": round(top10, 3),
            "essentials_seen": len(set(anchors) & set(cat.essentials)), "ess_total": len(cat.essentials),
            "store": {"items": [{"label": it.get("label", ""), "kind": it.get("kind", ""), "vids": it.get("vids", [])[:60],
                                 "watched": it.get("watched", [])[:300], "texts": it.get("texts", [])[:150],
                                 "neg": it.get("neg", [])[:200]} for it in items if it.get("kind") not in ("watch", "feedback")]}}
