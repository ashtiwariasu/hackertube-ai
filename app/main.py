"""HackerTube AI: the YouTube videos Hacker News keeps recommending, personalised."""
import asyncio, collections, datetime, gc, hashlib, io, json, logging, os, re, secrets, threading, time, urllib.parse, zipfile
from html import escape

import anthropic
import httpx
import numpy as np
import stripe
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse, Response)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import aiprofile as aip
from . import anything as anyx
from . import datarelease
from . import notes as nts
from . import screenshot as shot
from . import sources as src
from .catalog import ADDED, Catalog, fmt_dur, fmt_views, iso_dur, slugify
from .store import make_store, now
from .web_source import WEB_COMM

log = logging.getLogger("hackertube")
logging.basicConfig(level=logging.INFO)

HERE = os.path.dirname(__file__)
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8080").rstrip("/")
SITE = "HackerTube AI"
SESSION_SECRET = os.environ.get("SESSION_SECRET", "dev-secret-change-me")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
PRICE_LABEL = "$5"
SECURE = BASE_URL.startswith("https://")
YT_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"

_boot_dir = datarelease.startup_dir(os.path.join(HERE, "data"))
cat = Catalog(_boot_dir)
datarelease.discard(_boot_dir)  # the release files are loaded; on Cloud Run /tmp is RAM
store = make_store()
NOTES_ON = os.environ.get("HT_NOTES", "0").strip().lower() in ("1", "true", "yes", "on")  # off unless switched on
PILOT_TOPIC = os.environ.get("PILOT_TOPIC", "").strip()   # a topic id, or * for every video; empty means no pay
ids = nts.Ids(os.environ.get("HANDLE_SALT", SESSION_SECRET))
watcher = nts.Watch(SESSION_SECRET)
nstore = nts.Stores(store)
_assessed = {"t": 0.0, "v": None}   # every note's assessment, cached for a minute


class _Busting:
    """A notes or judgments collection that drops the cached assessment whenever it changes."""
    def __init__(self, coll):
        self.c = coll

    def __getattr__(self, k):
        return getattr(self.c, k)

    def add(self, did, doc):
        ok = self.c.add(did, doc)
        _assessed["v"] = None
        return ok

    def update(self, did, fields):
        self.c.update(did, fields)
        _assessed["v"] = None

    def delete(self, did):
        self.c.delete(did)
        _assessed["v"] = None


nstore.notes, nstore.judgments = _Busting(nstore.notes), _Busting(nstore.judgments)
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}$")
REF_COOKIE = "ht_ref"   # the last partner link followed, for 30 days: that partner's reviews are not yours to rate
cat.embed(["warm up"])  # load the search model before serving; idle CPU is throttled on Cloud Run
signer = URLSafeTimedSerializer(SESSION_SECRET, salt="ht-session")
state_signer = URLSafeTimedSerializer(SESSION_SECRET, salt="ht-oauth")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(GZipMiddleware, minimum_size=800)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))
GA_ID = os.environ.get("GA_MEASUREMENT_ID", "").strip()
POSTHOG_KEY = os.environ.get("POSTHOG_KEY", "").strip()
POSTHOG_HOST = os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com").rstrip("/")
POSTHOG_ASSETS = POSTHOG_HOST.replace(".i.posthog.com", "-assets.i.posthog.com")
POSTHOG_PUBLIC = os.environ.get("POSTHOG_PUBLIC_HOST", BASE_URL + "/ph").rstrip("/")  # same-origin proxy, dodges ad blockers
templates.env.globals.update(SITE=SITE, BASE_URL=BASE_URL, PRICE=PRICE_LABEL,
                             YEAR=datetime.date.today().year,
                             GA_ID=GA_ID, POSTHOG_KEY=POSTHOG_KEY, POSTHOG_HOST=POSTHOG_HOST,
                             POSTHOG_PUBLIC=POSTHOG_PUBLIC, NOTES_ON=NOTES_ON,
                             OPERATOR={k: os.environ.get(f"OPERATOR_{k.upper()}", "").strip() for k in ("name", "place", "contact")},
                             SOURCE_URL=os.environ.get("SOURCE_URL", "https://github.com/ashtiwariasu/hackertube-ai").rstrip("/"))


PH_PASS_REQ = ("content-type", "content-encoding", "user-agent", "accept", "origin", "referer")  # httpx picks its own accept-encoding and decodes
PH_PASS_RESP = ("content-type", "cache-control", "etag", "last-modified", "access-control-allow-origin")  # body is re-sent decoded


@app.api_route("/ph/{path:path}", methods=["GET", "POST", "OPTIONS"])
async def posthog_proxy(request: Request, path: str):
    """Reverse proxy for PostHog so events go through our own domain (ad blockers drop third-party calls)."""
    if not POSTHOG_KEY:
        return PlainTextResponse("", 404)
    upstream = POSTHOG_ASSETS if path.startswith("static/") else POSTHOG_HOST
    url = f"{upstream}/{path}"
    if request.url.query:
        url += "?" + request.url.query
    headers = {k: v for k, v in request.headers.items() if k.lower() in PH_PASS_REQ}
    headers["X-Forwarded-For"] = client_ip(request)
    headers["X-Forwarded-Host"] = request.headers.get("host", "")
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            r = await client.request(request.method, url, headers=headers, content=await request.body())
    except httpx.HTTPError as e:
        log.info("posthog proxy failed: %r", e)
        return PlainTextResponse("", 502)
    resp_headers = {k: v for k, v in r.headers.items() if k.lower() in PH_PASS_RESP}
    return Response(content=r.content, status_code=r.status_code, headers=resp_headers)


def track_server(event, distinct_id, props=None):
    """Fire-and-forget server-side PostHog event (purchases happen on the webhook, not in a browser)."""
    if not POSTHOG_KEY or not distinct_id:
        return
    body = {"api_key": POSTHOG_KEY, "event": event, "distinct_id": distinct_id,
            "properties": {"$lib": "hackertube-server", **(props or {})}}
    def send():
        try:
            httpx.post(POSTHOG_HOST + "/capture/", json=body, timeout=5)
        except Exception as e:  # noqa: BLE001
            log.info("posthog capture failed: %r", e)
    threading.Thread(target=send, daemon=True).start()


# ---------------- helpers ----------------
def current_user(request: Request):
    raw = request.cookies.get("ht_s")
    if not raw:
        return None
    try:
        data = signer.loads(raw, max_age=60 * 60 * 24 * 90)
    except BadSignature:
        return None
    u = store.get(data["uid"])
    if u is not None:
        u["uid"] = data["uid"]
    return u


def watched_set(u):
    if not u:
        return set()
    return (set(u.get("yt_liked_in_catalog", [])) | set(u.get("hist_in_catalog", [])) | set(u.get("shot_in_catalog", []))
            | set(u.get("paste_watched", [])) | set(u.get("seen", [])) | set(watched_on_site(u)))


WATCHED_PCT, ABANDON_PCT, ABANDON_SECS = 0.6, 0.2, 30


def really_watched(w):
    """Reached 60% and actually spent time on it (a seek to the end alone doesn't count)."""
    dur = w.get("dur") or 0
    return (w.get("pct") or 0) >= WATCHED_PCT and (w.get("secs") or 0) >= min(0.3 * dur, 900)


def watched_on_site(u):
    return [v for v, w in (u.get("watch") or {}).items() if really_watched(w)]



def abandoned_on_site(u):
    return [v for v, w in (u.get("watch") or {}).items()
            if (w.get("pct") or 0) < ABANDON_PCT and (w.get("secs") or 0) >= ABANDON_SECS]


def page(request, name, ctx=None, status=200):
    u = current_user(request)
    base = {"request": request, "user": u, "pro": bool(u and u.get("pro")),
            "likes": u.get("likes", []) if u else [], "later": u.get("later", []) if u else [],
            "hidden": u.get("hidden", []) if u else [], "seen": u.get("seen", []) if u else [],
            "canonical": BASE_URL + request.url.path, "client_ready": bool(GOOGLE_CLIENT_ID)}
    base.update(ctx or {})
    resp = templates.TemplateResponse(request, name, base, status_code=status)
    resp.headers["Cache-Control"] = "private, no-store" if u else "public, max-age=300"
    resp.headers["Vary"] = "Cookie"  # a cached anonymous page must not be shown after sign-in
    return resp


def safe_next(nxt: str) -> str:
    return nxt if nxt and nxt.startswith("/") and not nxt.startswith("//") else "/"


SOURCE_WEIGHT = {"pastes": 1.3, "aiprofile": 1.2, "screenshots": 1.2, "email": 0.5, "github": 1.0, "hn": 1.0, "youtube": 1.0, "bluesky": 0.8, "website": 1.0,
                 "mastodon": 0.8, "stackoverflow": 1.0, "discord": 0.6, "history": 1.5}

SOURCE_DEFS = [
    {"name": "email", "kind": "auto", "badge": "@", "title": "Find my public profiles from my email", "primary": True,
     "what": "Looks up your sign-in email on Gravatar and GitHub. Shows you what it finds before using it."},
    {"name": "pastes", "kind": "oauth", "badge": "+", "title": "Things you pasted on the home page", "primary": True,
     "auth_url": "/#paste", "what": "Links, screenshots, handles or a few words about you. Kept as a short profile, never the raw text."},
    {"name": "aiprofile", "kind": "oauth", "badge": "AI", "title": "Ask your AI about you", "primary": True,
     "auth_url": "/ask-your-ai",
     "what": "Paste what ChatGPT, Claude or Gemini knows about your interests. We keep a short taste summary, not the text."},
    {"name": "screenshots", "kind": "shots", "badge": "IMG", "title": "Screenshot of your YouTube", "primary": True,
     "what": "Screenshot your history, liked videos or subscriptions. We read the titles once and keep only the matches."},
    {"name": "picks", "kind": "picks", "badge": "5", "title": "Pick five videos you love", "primary": True,
     "what": "No accounts needed. Tap a few HN classics you already know and like."},
    {"name": "github", "kind": "handle", "badge": "GH", "title": "GitHub", "primary": True, "placeholder": "username",
     "what": "Starred repos, your repos and bio. Public data, no sign-in."},
    {"name": "hn", "kind": "handle", "badge": "Y", "title": "Hacker News", "primary": True, "placeholder": "username",
     "what": "Your comments and submissions, and which of these talks you recommended yourself."},
    {"name": "youtube", "kind": "oauth", "badge": "YT", "title": "YouTube likes and subscriptions", "primary": True,
     "auth_url": "/auth/youtube?next=/connect",
     "what": "Read-only. Google shows a warning screen until our app is verified."},
    {"name": "bluesky", "kind": "handle", "badge": "BS", "title": "Bluesky", "placeholder": "name.bsky.social",
     "what": "Your posts and who you follow."},
    {"name": "website", "kind": "handle", "badge": "www", "title": "Personal site or blog", "placeholder": "https://",
     "what": "The page and its feed, if it has one."},
    {"name": "mastodon", "kind": "handle", "badge": "M", "title": "Mastodon", "placeholder": "name@instance.social",
     "what": "Your profile and recent posts."},
    {"name": "stackoverflow", "kind": "handle", "badge": "SO", "title": "Stack Overflow",
     "placeholder": "stackoverflow.com/users/12345/name", "what": "Your top tags and questions."},
    {"name": "discord", "kind": "oauth", "badge": "D", "title": "Discord servers", "auth_url": "/auth/discord",
     "what": "The names of servers you're in. Nothing else."},
    {"name": "history", "kind": "file", "badge": "TK", "title": "YouTube watch history (Takeout file)",
     "what": "Upload the file Google Takeout gives you. We keep only matches against the catalogue."},
]
SOURCE_NAMES = {d["name"] for d in SOURCE_DEFS}
SOURCE_CACHE = {}  # (name, handle) -> (time, result); spares the public APIs on retries and repeats
CACHE_TTL = 6 * 3600


def pick_candidates(n=24):
    """A fixed, varied set of well-known classics: the best evergreen video from each area, then fill."""
    seen, out = set(), []
    for area in cat.areas:
        for cid in (c["id"] for c in area["cats"]):
            best = next((cat.by_vid[v] for v in sorted(cat.cats[cid]["vids"], key=lambda x: -cat.by_vid[x]["score"])
                         if v not in seen and cat.by_vid[v]["shelf"] == "evergreen" and cat.by_vid[v]["views"] > 200_000), None)
            if best:
                seen.add(best["vid"])
                out.append(best)
                break
    for v in cat.ranked:
        if len(out) >= n:
            break
        if v["vid"] not in seen and v["views"] > 200_000:
            seen.add(v["vid"])
            out.append(v)
    return out[:n]


PICK_CANDIDATES = pick_candidates()


def _swap_catalog(data_dir):
    global cat, PICK_CANDIDATES
    new = Catalog(data_dir)
    datarelease.discard(data_dir)
    new._model = cat._model  # keep the loaded search model
    cat = new
    PICK_CANDIDATES = pick_candidates()
    SOURCE_CACHE.clear()
    gc.collect()  # hand the old catalogue's memory back now rather than at the next collection


@app.middleware("http")
async def partner_ref(request: Request, call_next):
    """Remember the last partner link someone followed, for 30 days, so they can't rate that partner's reviews.
    Links are /v/...?via=<handle> share links and /r/<handle> public pages."""
    resp = await call_next(request)
    if NOTES_ON and request.method == "GET" and resp.status_code < 400:
        h = request.query_params.get("via", "")
        m = re.match(r"^/r/(r[a-z2-7]{6})$", request.url.path)
        h = h if nts.HANDLE_RE.match(h or "") else (m.group(1) if m else "")
        if h:
            resp.set_cookie(REF_COOKIE, f"{h}.{int(nts.clock())}", max_age=nts.REF_DAYS * 86400, httponly=True,
                            secure=SECURE, samesite="lax")
            resp.headers["Cache-Control"] = "private, no-store"
    return resp


@app.middleware("http")
async def release_check(request: Request, call_next):
    datarelease.maybe_reload(_swap_catalog)
    if NOTES_ON and nts.clock() - _added_sync["t"] >= 60:
        await asyncio.to_thread(sync_added)
    return await call_next(request)


def hn_recs(u):
    return list((((u or {}).get("sources") or {}).get("hn") or {}).get("catalog_vids") or [])


def picks(u):
    return list((((u or {}).get("sources") or {}).get("picks") or {}).get("vids") or [])


def amount(n, full):
    """How much weight a list of n videos earns: one like is a hint, `full` or more is a pattern."""
    return min(1.0, n / float(full))


def rebuild_profile(u):
    """Combine every connected source into one taste vector plus readable labels. Lists of videos count by
    how many there are, so one video liked on a first visit can't outweigh a whole connected source."""
    vecs = u.get("vecs") or {}
    parts = [(SOURCE_WEIGHT.get(n, 1.0), v) for n, v in vecs.items() if v]
    watch = u.get("watch") or {}
    done = [v for v in watch if really_watched(watch[v])]
    for w, full, vids in ((1.5, 5, u.get("likes") or []), (2.0, 5, u.get("yt_liked_in_catalog") or []),
                          (1.5, 10, u.get("hist_in_catalog") or []), (2.0, 5, hn_recs(u)), (1.5, 5, picks(u)),
                          (2.0, 5, u.get("shot_in_catalog") or []), (1.5, 5, u.get("ai_in_catalog") or []),
                          (1.8, 5, u.get("paste_in_catalog") or []), (2.0, 5, done), (1.5, 10, u.get("seen") or [])):
        vids = list(vids)[-300:]
        parts.append((w * amount(len(vids), full), cat.vids_vector(vids)))
    vec = cat.combine(parts)
    # "not for me" is a clear signal; leaving a video early is a faint one (they may come back to it)
    hidden, left = list(u.get("hidden") or [])[-200:], abandoned_on_site(u)[-200:]
    neg = cat.combine([(1.0, cat.vids_vector(hidden)), (0.5, cat.vids_vector(left))])
    if vec is not None and neg is not None:
        k = 0.35 * amount(len(hidden) + 0.5 * len(left), 3)
        v = np.asarray(vec, dtype=np.float32) - k * np.asarray(neg, dtype=np.float32)
        vec = [round(float(x), 5) for x in v / (np.linalg.norm(v) + 1e-9)]
    return {"vec": vec, "interests": cat.interests(vec) if vec else []}


SOURCE_REASON = {"youtube": "what you watch on YouTube", "aiprofile": "what your AI said about you",
                 "pastes": "the videos you pasted", "screenshots": "the videos in your screenshots",
                 "history": "your watch history", "hn": "what you write on Hacker News", "github": "your GitHub work",
                 "email": "your public profile", "website": "your website", "bluesky": "your Bluesky posts",
                 "mastodon": "your Mastodon posts", "stackoverflow": "your Stack Overflow answers", "discord": "your Discord"}


def profile_anchors(u):
    """Someone's separate interests, each with the reason it gives a pick (see Catalog.librarian): what they
    told their AI about, the videos they liked, watched, saved or named, and each connected source as a whole.
    A single video says less than a habit, so video reasons start weaker and reach full strength at six videos."""
    out = []
    prof = ((u.get("sources") or {}).get("aiprofile") or {}).get("profile") or {}
    topics = list(dict.fromkeys(t.strip() for k in ("topics", "learning", "work") for t in (prof.get(k) or [])
                                if isinstance(t, str) and t.strip()))[:24]
    if topics:
        for t, v in zip(topics, cat.embed_cached(topics)):
            out.append(("topic", f"For your interest in {t}", v, 0.0, ""))
    watch = u.get("watch") or {}
    liked, watched, saved = "Because you liked \u201c{}\u201d", "Because you watched \u201c{}\u201d", "Like \u201c{}\u201d, which you saved"
    listed = ([(v, liked) for v in (u.get("likes") or [])[-15:]]
              + [(v, watched) for v in [v for v in watch if really_watched(watch[v])][-15:]]
              + [(v, saved) for v in (u.get("later") or [])[-10:]]
              + [(v, liked) for v in (u.get("yt_liked_in_catalog") or [])[-15:]]
              + [(v, watched) for v in (u.get("hist_in_catalog") or [])[-15:] + (u.get("shot_in_catalog") or [])[-10:]
                 + (u.get("paste_in_catalog") or [])[-10:]]
              + [(v, "Because you recommended \u201c{}\u201d") for v in hn_recs(u)[-10:]]
              + [(v, "Because you picked \u201c{}\u201d") for v in picks(u)[-10:] + (u.get("ai_in_catalog") or [])[-10:]])
    items, have = [], set()
    for vid, how in listed:
        if vid in cat.by_vid and vid not in have:
            have.add(vid)
            items.append((vid, how))
    bias = -1.0 * (1 - amount(len(items), 6))
    for vid, how in items:
        x = cat.by_vid[vid]
        out.append(("item", how.format(x["title"][:60]), cat.emb[x["i"]], bias, vid))
    for name, v in (u.get("vecs") or {}).items():
        if v:
            out.append(("source", f"Close to {SOURCE_REASON.get(name, 'what you told us')}", v, -0.25, ""))
    return out


FEED_FIELDS = ("sources", "likes", "later", "hidden", "seen", "watch", "yt_liked_in_catalog", "yt_channels_matched",
               "hist_in_catalog", "shot_in_catalog", "ai_in_catalog", "paste_in_catalog", "paste_watched")
_feed_cache = {}


def save_profile(u, extra=None):
    """Recompute the profile and overwrite the user's document (merge can't drop map keys)."""
    u.update(extra or {})
    u.update(rebuild_profile(u))
    doc = {k: v for k, v in u.items() if k != "uid"}
    store.replace(u["uid"], doc)
    return u


def personal_data(u):
    """The personal shortlist, with the reason for each pick. Cached per person until
    anything that shapes it changes. The taste vector is recomputed here, so a change to how signals are
    weighed reaches everyone at once; when it moved, the stored copy is brought up to date."""
    if not u or not u.get("vec"):
        return None
    fp = hashlib.md5(json.dumps([u.get(k) for k in FEED_FIELDS], sort_keys=True, default=str).encode()).hexdigest()
    hit = _feed_cache.get(u.get("uid"))
    if hit and hit[0] == (fp, id(cat)) and time.time() - hit[1] < 1800:
        return hit[2]
    prof = rebuild_profile(u)
    if prof["vec"] and (prof["vec"] != u.get("vec") or prof["interests"] != u.get("interests")) and u.get("uid"):
        u.update(prof)
        try:
            store.upsert(u["uid"], {"vec": prof["vec"], "interests": prof["interests"]})
        except Exception as e:  # noqa: BLE001 - the feed still shows; the stored copy updates next time
            log.warning("profile refresh failed uid=%s: %r", u.get("uid"), e)
    watched = watched_set(u)
    recs = hn_recs(u)
    fmt = (((u.get("sources") or {}).get("aiprofile") or {}).get("profile") or {}).get("format") or ""
    feed, reasons = cat.librarian(u["vec"], profile_anchors(u),
                                  exclude=watched | set(u.get("likes", [])) | set(recs) | set(picks(u)) | set(u.get("hidden") or []),
                                  followed=u.get("yt_channels_matched", []),
                                  boost_vids=cat.co_recommended(recs) if recs else (), fmt=fmt, n=60)
    unseen = [cat.by_vid[v] for v in cat.essentials if v not in watched and v in cat.by_vid]
    seen_ess = len(cat.essentials) - len(unseen)
    data = {"feed": feed, "reasons": reasons, "unseen": unseen, "seen_ess": seen_ess, "ess_total": len(cat.essentials),
            "interests": u.get("interests") or []}
    if u.get("uid"):
        if len(_feed_cache) > 2000:
            _feed_cache.clear()
        _feed_cache[u["uid"]] = ((fp, id(cat)), time.time(), data)
    return data


def source_states(u):
    srcs = u.get("sources") or {}
    out = {}
    for d in SOURCE_DEFS:
        n, rec = d["name"], (srcs.get(d["name"]) or {})
        if n == "youtube":
            on = bool(u.get("yt_connected_at"))
            label = (f"{u.get('yt_liked_total', 0)} likes · {u.get('yt_subs_total', 0)} subscriptions · "
                     f"{len(u.get('yt_liked_in_catalog') or [])} in the catalogue") if on else ""
        elif n == "history":
            on = bool(u.get("hist_imported_at"))
            label = f"{u.get('hist_total', 0)} videos · {len(u.get('hist_in_catalog') or [])} HN picks" if on else ""
        elif n == "pastes":
            on = bool(rec.get("connected_at"))
            label = rec.get("label", "") if on else ""
        elif n == "aiprofile":
            on = bool(rec.get("connected_at"))
            label = rec.get("label", "") if on else ""
        elif n == "screenshots":
            on = bool(rec.get("connected_at"))
            label = rec.get("label", "") if on else ""
        elif n == "picks":
            on = bool(rec.get("vids"))
            label = f"{len(rec.get('vids') or [])} videos picked" if on else ""
        elif n == "email":
            on = rec.get("on", True)
            f = rec.get("found") or {}
            hits = [x for x in (f.get("github") and "GitHub @" + f["github"], f.get("website")) if x]
            label = ("Found " + " and ".join(hits)) if hits else ("Nothing public found for this email" if rec else "")
        else:
            on = bool(rec.get("connected_at"))
            label = rec.get("label", "")
        out[n] = {"on": on, "label": label, "handle": rec.get("handle", "")}
    return out


def profile_json(u):
    watched = watched_set(u)
    return {"interests": u.get("interests") or [], "sources": source_states(u), "pro": bool(u.get("pro")),
            "seen_ess": sum(1 for v in cat.essentials if v in watched), "ess_total": len(cat.essentials),
            "has_feed": bool(u.get("vec"))}


async def fetch_source(client, name, handle):
    if name == "github":
        return await src.github(client, handle)
    if name == "hn":
        return await src.hackernews(client, handle, cat)
    if name == "bluesky":
        return await src.bluesky(client, handle)
    if name == "website":
        return await src.website(client, handle)
    if name == "mastodon":
        return await src.mastodon(client, handle)
    if name == "stackoverflow":
        return await src.stackoverflow(client, handle)
    raise src.SourceError("Unknown source.")


async def attach_source(u, name, res):
    """Embed a fetched source's texts and record it on the user (in memory; caller saves)."""
    vec = await asyncio.to_thread(cat.text_vector, res["texts"])
    sources, vecs = dict(u.get("sources") or {}), dict(u.get("vecs") or {})
    sources[name] = {"handle": res["handle"], "label": res["label"], "connected_at": now(),
                     "count": len(res["texts"]), "catalog_vids": res.get("catalog_vids", [])}
    vecs[name] = vec
    u["sources"], u["vecs"] = sources, vecs
    log.info("funnel source-connected uid=%s source=%s texts=%d", u.get("uid"), name, len(res["texts"]))


# ---------------- pages ----------------
REF_COMM = {"news.ycombinator.com": "hn", "stackoverflow.com": "se:stackoverflow", "superuser.com": "se:superuser",
            "serverfault.com": "se:serverfault", "askubuntu.com": "se:askubuntu", "mathoverflow.net": "se:mathoverflow"}
AREA_SHORT = {"software": "Software", "systems-security": "Systems & security", "ai-data": "AI & data", "math-theory": "Maths",
              "hardware": "Hardware", "computing-history-culture": "Computing history", "design-graphics-media": "Design & media",
              "business-economics": "Business", "politics-history-society": "Politics & history", "physics-space": "Physics & space",
              "life-sciences-health": "Life & mind", "earth-energy-environment": "Earth & energy", "engineering-making": "Engineering & making",
              "games": "Games", "arts-entertainment": "Arts & film", "ideas-education": "Ideas & learning"}


def silent_default(request):
    """What to lead with before the visitor does anything: preset link, referrer, then language."""
    into = [x for x in request.query_params.get("into", "").split(",") if x]
    areas = [a for a in into if a in cat.area_by_id]
    comms = [c for c in into if c in cat.comms] + [c.replace("-", ":", 1) for c in into if c.replace("-", ":", 1) in cat.comms]
    why = "from your link" if (areas or comms) else ""
    if not areas and not comms:
        host = (urllib.parse.urlparse(request.headers.get("referer", "")).hostname or "").lower().replace("www.", "")
        c = REF_COMM.get(host)
        if not c and host.endswith(".stackexchange.com"):
            c = "se:" + host.split(".")[0]
        if c and c in cat.comms:
            comms, why = [c], f"because you came from {cat.comms[c]['label']}"
    if not areas and not comms:
        lang = request.headers.get("accept-language", "")[:2].lower()
        c = {"ru": "se:ru.stackoverflow", "es": "se:es.stackoverflow", "pt": "se:pt.stackoverflow"}.get(lang)
        if c and c in cat.comms:
            comms, why = [c], "in your language"
    return areas, comms, why


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    u = current_user(request)
    areas, comms, why = silent_default(request)
    rows, pool_n = cat.feed_rows(areas, comms, seed="home")
    rows = drop_rejected(rows, u)
    cont = continue_row(u) if u else []
    scope_label = ", ".join([AREA_SHORT.get(a, a) for a in areas] + [cat.comms[c]["label"] for c in comms])
    skip = set((u or {}).get("hidden") or []) | set((u or {}).get("seen") or [])
    starter = cat.starter(8, seed=datetime.date.today().isoformat(), skip=skip)
    w = this_week() if NOTES_ON else None
    ctx = {
        "week": w, "week_videos": week_videos(w), "week_left": spots_left(w), "pot_cents": pot_now() if w else 0,
        "money": nts.money, "review_cents": nts.REVIEW_CENTS,
        "starter": starter, "starter_proof": {v["vid"]: cat.proof(v) for v in starter}, "waiting": waiting_videos(),
        "new_finds": sorted(ADDED.values(), key=lambda v: -v["added_ts"])[:8] if NOTES_ON else [],
        "rows": rows, "pool_n": pool_n, "continue": cont, "scope_label": scope_label, "scope_why": why, "scope_areas": areas, "scope_comms": comms,
        "chips": [{"id": a["id"], "label": AREA_SHORT.get(a["id"], a["name"]), "n": a["count"]} for a in cat.areas],
        "classics": cat.daily_mix(cat.ranked[:300], 12, "c"),
        "gems": cat.daily_mix(cat.gems[:400], 8, "g"),
        "recent": cat.daily_mix(cat.recent[:300], 8, "r"),
        "longs": cat.daily_mix(cat.longs[:300], 4, "l"),
        "areas": cat.areas,
        "total": len(cat.videos),
        "communities": sorted(cat.comms.values(), key=lambda c: -len(c["vids"])),
        "personal": personal_data(u),
        "title": "HackerTube AI: what to watch next, from the videos Hacker News and Stack Overflow keep recommending",
        "description": f"{len(cat.videos):,} YouTube talks, lectures and documentaries that Hacker News "
                       "has recommended for 19 years. Connect YouTube to see which HN essentials you "
                       "haven't watched and get a feed tuned to you.",
    }
    return page(request, "home.html", ctx)


def video_ld(v):
    ld = {
        "@context": "https://schema.org", "@type": "VideoObject",
        "name": v["title"], "description": v["summary"],
        "thumbnailUrl": [f"https://i.ytimg.com/vi/{v['vid']}/hqdefault.jpg"],
        "uploadDate": v["published"] or "2010-01-01",
        "embedUrl": f"https://www.youtube-nocookie.com/embed/{v['vid']}",
        "contentUrl": f"https://www.youtube.com/watch?v={v['vid']}",
        "author": {"@type": "Person", "name": v["channel"]},
        "interactionStatistic": {"@type": "InteractionCounter",
                                 "interactionType": {"@type": "WatchAction"},
                                 "userInteractionCount": v["views"]},
    }
    if v["dur"]:
        ld["duration"] = iso_dur(v["dur"])
    return ld


@app.get("/v/{vid}", response_class=HTMLResponse)
@app.get("/v/{vid}/{slug}", response_class=HTMLResponse)
def video(request: Request, vid: str, slug: str = ""):
    v = cat.get(vid)
    if not v:
        return page(request, "404.html", {"title": "Not found", "description": ""}, 404)
    if slug != v["slug"]:
        return RedirectResponse(v["url"], status_code=301)
    cats = [cat.cats[c] for c in v["cats"] if c in cat.cats]
    crumbs = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Home", "item": BASE_URL + "/"}]}
    if cats:
        crumbs["itemListElement"].append({"@type": "ListItem", "position": 2, "name": cats[0]["name"],
                                          "item": f"{BASE_URL}/topics/{cats[0]['id']}"})
    crumbs["itemListElement"].append({"@type": "ListItem", "position": len(crumbs["itemListElement"]) + 1,
                                      "name": v["title"], "item": BASE_URL + v["url"]})
    rec = v["hn"]["recommenders"]
    w = ((u or {}).get("watch") or {}).get(vid) if (u := current_user(request)) else None
    resume = int(w["pos"]) if w and 0.05 < (w.get("pct") or 0) < 0.9 and (w.get("pos") or 0) > 30 else 0
    if (request.query_params.get("t") or "").isdigit():
        resume = int(request.query_params["t"])
    h, rem = divmod(resume, 3600)
    resume_s = f"{h}:{rem // 60:02d}:{rem % 60:02d}" if h else f"{rem // 60}:{rem % 60:02d}"
    via_note = None
    via = request.query_params.get("via", "")
    if NOTES_ON and nts.HANDLE_RE.match(via):
        n = nstore.notes.get(nts.doc_id(vid, next((x["uid"] for x in nstore.notes.by("handle", via) if x.get("uid")), "")))
        if n and n.get("mod") == "ok" and n.get("handle") == via:
            via_note = {"name": display_name(n["uid"]), "text": n["text"], "stance": n.get("stance"), "handle": via}
    feat = featured(u, vid) if NOTES_ON else None
    if feat:
        msg = request.query_params.get("msg", "")
        lv = partner_level(u["uid"]) if feat["partner"] else nts.LEVELS[0]
        feat.update(msg=nts.claim_message(msg, lv, feat["week"]) if msg in nts.CLAIM_MSGS else "",
                    ok=msg in ("claimed", "released"), level=lv, opens=nts.when_utc(nts.opens_at(feat["week"], lv)),
                    open_now=nts.clock() >= nts.opens_at(feat["week"], lv), money=nts.money)
    if v.get("added"):
        return added_video_page(request, v, cats, crumbs, resume, resume_s, via_note, feat)
    ctx = {
        "v": v, "cats": cats, "mentions": cat.mentions.get(vid, []), "resume": resume, "resume_s": resume_s,
        "via_note": via_note, "feat": feat,
        "co": cat.co_recommended(vid), "similar": cat.similar(vid, 12),
        "title": f"{v['title']} | Why {'the open web links it' if v.get('primary') == WEB_COMM else 'Hacker News recommends it'}",
        "description": (v["why_hn"] + f" Recommended by {rec} HN commenters." if rec else v["why_hn"])[:300],
        "og_image": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
        "jsonld": [video_ld(v), crumbs],
    }
    return page(request, "video.html", ctx)


@app.get("/topics", response_class=HTMLResponse)
def topics(request: Request):
    return page(request, "topics.html", {
        "areas": cat.areas,
        "title": "Topics: the best YouTube videos by subject, according to Hacker News",
        "description": f"Browse {len(cat.videos):,} Hacker News recommended videos across "
                       f"{sum(len(a['cats']) for a in cat.areas)} topics, from functional programming "
                       "to cosmology."})


def _load_topic_intros():
    p = os.path.join(os.path.dirname(__file__), "data", "topic_intros.json")
    try:
        return json.load(open(p, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


TOPIC_INTROS = _load_topic_intros()  # written by scripts/gen_topic_intros.py; ships with the app, not the data release


@app.get("/topics/{cid}", response_class=HTMLResponse)
def topic(request: Request, cid: str):
    c = cat.cats.get(cid)
    if not c or not c["vids"]:
        return page(request, "404.html", {"title": "Not found", "description": ""}, 404)
    vids = sorted((cat.by_vid[x] for x in c["vids"]), key=lambda v: -v["score"])
    n_web = sum(1 for v in vids if not v.get("comm"))
    top = vids[:10]
    ld = {"@context": "https://schema.org", "@type": "ItemList",
          "itemListElement": [{"@type": "ListItem", "position": i + 1, "url": BASE_URL + v["url"],
                               "name": v["title"]} for i, v in enumerate(top)]}
    written = TOPIC_INTROS.get(cid) or {}
    return page(request, "list.html", {
        "heading": f"The best {c['name'].lower()} videos, according to Hacker News" + (" and the open web" if n_web else ""),
        "intro": (f"{len(vids) - n_web} videos about {c['name'].lower()} that Hacker News and Stack Exchange keep "
                  f"pointing each other to, ranked by how many separate months they were recommended, then {n_web} more "
                  f"that at least 2 widely read websites link to. Part of {c['area']}.") if n_web else (
                 f"{len(vids)} videos about {c['name'].lower()} that Hacker News commenters keep "
                 f"pointing each other to, ranked by how many separate months they were recommended. "
                 f"Part of {c['area']}."),
        "paragraphs": [written.get("p1"), written.get("p2")] if written else [],
        "related": [cat.cats[r] for r in cat.related_cats(cid, 8)],
        "videos": vids[:120], "crumb": c["area"], "waiting": waiting_videos(8, set(c["vids"])),
        "title": f"Best {c['name']} videos on YouTube, recommended by Hacker News" + (" and the open web" if n_web else ""),
        "description": f"The top {c['name'].lower()} talks and videos Hacker News and Stack Exchange recommend: "
                       + ", ".join(v["title"] for v in top[:3])[:220],
        "jsonld": [ld]})


LISTS = {
    "best": ("The best YouTube videos of all time, according to Hacker News",
             "The all-time classics: videos recommended across the most separate months on Hacker News, Stack Overflow and Stack Exchange.",
             lambda: cat.ranked[:200]),
    "hidden-gems": ("Hidden gems: great videos Hacker News loves that few people have watched",
                    "Strongly recommended on Hacker News, under 60,000 views on YouTube.",
                    lambda: cat.gems[:200]),
    "recent": ("Recent Hacker News favourites",
               "Videos Hacker News has been recommending in 2025 and 2026.",
               lambda: cat.recent[:200]),
    "longs": ("HackerTube Longs: the best long talks and lectures recommended by Hacker News",
              "Over an hour each. The deep-dive talks and lecture series HN keeps sending people to.",
              lambda: cat.longs[:200]),
}


def make_list_route(name):
    def handler(request: Request):
        h, intro, fn = LISTS[name]
        return page(request, "list.html", {"heading": h, "intro": intro, "videos": fn(),
                                           "title": h, "description": intro})
    app.add_api_route(f"/{name}", handler, methods=["GET"], response_class=HTMLResponse, name=f"list_{name}")


for _name in LISTS:
    make_list_route(_name)


@app.get("/search/", response_class=HTMLResponse)
@app.get("/search", response_class=HTMLResponse)
def search(request: Request, q: str = ""):
    res = cat.search(q) if q else []
    resp = page(request, "list.html", {
        "heading": f"Results for “{q}”" if q else "Search", "intro": "Search understands topics, not just keywords.",
        "videos": res, "q": q, "noindex": True,
        "title": f"{q} | Hacker News recommended videos" if q else "Search", "description": ""})
    return resp


@app.get("/karpathy-hackertube", response_class=HTMLResponse)
def karpathy(request: Request):
    faq = [
        ("What is HackerTube?",
         "HackerTube is a weekend project Andrej Karpathy shared in October 2026. Claude read 43 million "
         "Hacker News comments from 2007 to 2026 and kept the 6,454 YouTube videos that HN commenters kept "
         "recommending to each other, across different people and different months. The original site is "
         "hackertube.org and its data is open."),
        ("Is HackerTube AI the same site as hackertube.org?",
         "No. HackerTube AI is an independent project built on HackerTube's open dataset (ODC-By 1.0). It is "
         "not affiliated with Andrej Karpathy, hackertube.org, Hacker News, Y Combinator, YouTube or Google. "
         "It adds Google sign-in, YouTube connect, watch-history import and a personal feed."),
        ("What does Pro do?",
         f"Pro is a one-time {PRICE_LABEL}. Connect your YouTube account or import your watch history and "
         "HackerTube AI shows which of the 500 Hacker News essentials you have not watched yet, and builds a "
         "feed of HN-recommended videos matched to your likes and subscriptions."),
        ("Can I build my own HackerTube?",
         "Yes. Karpathy's post suggests pointing Claude at the open data zip and asking for your own version. "
         "Our guide walks through it step by step."),
        ("Do I need an account?",
         "No. Browsing, search, topics and likes work without signing in. Sign in only to sync across devices "
         "or to personalise with YouTube."),
    ]
    ld = {"@context": "https://schema.org", "@type": "FAQPage", "mainEntity": [
        {"@type": "Question", "name": q, "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in faq]}
    return page(request, "karpathy.html", {
        "faq": faq, "jsonld": [ld], "classics": cat.ranked[:8], "total": len(cat.videos),
        "title": "Karpathy's HackerTube, personalised: your own Hacker News YouTube feed",
        "description": "Karpathy's HackerTube filtered 43M Hacker News comments down to the YouTube videos HN keeps "
                       "recommending. HackerTube AI adds your own feed: connect YouTube, see which HN essentials "
                       "you haven't watched."})


@app.get("/build-your-own-hackertube", response_class=HTMLResponse)
def build_own(request: Request):
    return page(request, "build.html", {
        "title": "How to build your own HackerTube with Claude Code (step by step)",
        "description": "Karpathy built HackerTube in about two hours with Claude Code. Here is how to fork the open "
                       "dataset and build your own version: YouTube API key, prompt, hosting and costs."})


@app.get("/pro", response_class=HTMLResponse)
def pro_page(request: Request):
    return page(request, "pro.html", {
        "title": f"HackerTube AI Pro: your personal Hacker News YouTube feed for {PRICE_LABEL}",
        "description": "Connect YouTube, import your watch history, and see the Hacker News essentials you've "
                       f"never watched. One-time {PRICE_LABEL}, no subscription.",
        "cancelled": request.query_params.get("cancelled"), "total": len(cat.videos)})


@app.get("/me", response_class=HTMLResponse)
def me(request: Request):
    u = current_user(request)
    if not u:
        return RedirectResponse("/auth/login?next=/me", 302)
    return page(request, "me.html", {
        "personal": personal_data(u), "title": "Your HackerTube", "description": "", "noindex": True,
        "liked_videos": [cat.get(v) for v in reversed(u.get("likes", [])) if cat.get(v)],
        "later_videos": [cat.get(v) for v in reversed(u.get("later", [])) if cat.get(v)],
        "msg": request.query_params.get("msg", ""), **my_notes(u)})


def my_notes(u):
    if not NOTES_ON:
        return {"note_rows": [], "note_rec": None}
    rows, rec = person_record(u["uid"])
    mine = partner_view(u) if (hacker_status(u) == "approved" and pilot()) else None
    return {"note_rows": rows, "note_rec": rec, "handle": ids.handle(u["uid"]), "rec_name": u.get("rec_name", ""),
            "panel": nts.PANEL, "qualify": nts.QUALIFY, "hacker": hacker_status(u), "hacker_month": mine, "pilot": pilot(),
            "money": nts.money}


@app.get("/about", response_class=HTMLResponse)
def about(request: Request):
    return page(request, "about.html", {"title": "About HackerTube AI", "total": len(cat.videos),
                                        "description": "How HackerTube AI works, where the data comes from, and credits."})


@app.get("/privacy", response_class=HTMLResponse)
def privacy(request: Request):
    return page(request, "privacy.html", {"title": "Privacy policy", "description": "HackerTube AI privacy policy."})


@app.get("/terms", response_class=HTMLResponse)
def terms(request: Request):
    return page(request, "terms.html", {"title": "Terms of service", "description": "HackerTube AI terms of service."})


# ---------------- SEO plumbing ----------------
@app.get("/robots.txt", response_class=PlainTextResponse)
def robots():
    slow = "".join(f"User-agent: {b}\nCrawl-delay: 30\n\n" for b in SLOW_BOTS)  # SEO-tool crawlers eat a third of requests
    return (f"User-agent: *\nAllow: /\nDisallow: /search\nDisallow: /me\nDisallow: /auth/\nDisallow: /api/\n\n"
            f"{slow}Sitemap: {BASE_URL}/sitemap.xml\n")


SLOW_BOTS = ("AhrefsBot", "YandexBot", "SemrushBot", "MJ12bot", "DotBot", "PetalBot", "DataForSeoBot")


VIDEOS_PER_SITEMAP = 5000


def _release_date():
    r = datarelease.current() or ""
    return f"{r[:4]}-{r[4:6]}-{r[6:8]}" if len(r) >= 8 and r[:8].isdigit() else datetime.date.today().isoformat()


def _urlset(entries):
    body = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for path, lastmod, freq, pri in entries:
        body.append(f"<url><loc>{escape(BASE_URL + path)}</loc><lastmod>{lastmod}</lastmod>"
                    f"<changefreq>{freq}</changefreq><priority>{pri}</priority></url>")
    body.append("</urlset>")
    return Response("\n".join(body), media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/sitemap.xml")
def sitemap():
    """Sitemap index: pages, topics, then videos in chunks, so crawlers can see which part changed."""
    rel = _release_date()
    maps = [("/sitemap-pages.xml", datetime.date.today().isoformat()), ("/sitemap-topics.xml", rel)]
    maps += [(f"/sitemap-videos-{i + 1}.xml", rel)
             for i in range((len(cat.videos) + VIDEOS_PER_SITEMAP - 1) // VIDEOS_PER_SITEMAP)]
    body = ['<?xml version="1.0" encoding="UTF-8"?>', '<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    body += [f"<sitemap><loc>{escape(BASE_URL + p)}</loc><lastmod>{d}</lastmod></sitemap>" for p, d in maps]
    body.append("</sitemapindex>")
    return Response("\n".join(body), media_type="application/xml", headers={"Cache-Control": "public, max-age=3600"})


@app.get("/sitemap-pages.xml")
def sitemap_pages():
    today = datetime.date.today().isoformat()
    urls = [("/", today, "daily", "1.0"), ("/karpathy-hackertube", today, "weekly", "0.9"),
            ("/build-your-own-hackertube", today, "monthly", "0.8"), ("/pro", today, "monthly", "0.7"),
            ("/topics", today, "weekly", "0.8"), ("/about", today, "monthly", "0.4"),
            ("/ask-your-ai", today, "monthly", "0.8"), ("/screenshot", today, "monthly", "0.8"),
            ("/communities", today, "weekly", "0.7")]
    urls += [(f"/{k}", today, "weekly", "0.8") for k in LISTS]
    urls += [(f"/c/{c['slug']}", today, "weekly", "0.7") for c in cat.comms.values() if len(cat.comms) > 1]
    if NOTES_ON and pilot_on():
        urls.append(("/week", today, "weekly", "0.9"))
    return _urlset(urls)


@app.get("/sitemap-topics.xml")
def sitemap_topics():
    rel = _release_date()
    return _urlset([(f"/topics/{c['id']}", rel, "weekly", "0.7") for a in cat.areas for c in a["cats"]])


@app.get("/sitemap-videos-{n}.xml")
def sitemap_videos(n: int):
    chunk = cat.videos[(n - 1) * VIDEOS_PER_SITEMAP:n * VIDEOS_PER_SITEMAP]
    if n < 1 or not chunk:
        return PlainTextResponse("", 404)
    rel = _release_date()
    return _urlset([(v["url"], rel, "monthly", "0.6") for v in chunk])


@app.get("/llms.txt", response_class=PlainTextResponse)
def llms():
    lines = [f"# {SITE}", "",
             f"> {len(cat.videos):,} YouTube videos that Hacker News commenters have recommended to each other "
             "since 2007, each with the real HN comments that recommended it. Built on the open HackerTube dataset "
             "(hackertube.org, ODC-By 1.0). Independent; not affiliated with hackertube.org or Andrej Karpathy.", "",
             "## Key pages", f"- [Best of all time]({BASE_URL}/best)", f"- [Hidden gems]({BASE_URL}/hidden-gems)",
             f"- [Long talks]({BASE_URL}/longs)", f"- [All topics]({BASE_URL}/topics)",
             f"- [What is Karpathy's HackerTube]({BASE_URL}/karpathy-hackertube)",
             f"- [Build your own HackerTube]({BASE_URL}/build-your-own-hackertube)", "", "## Topics"]
    for a in cat.areas:
        for c in a["cats"]:
            lines.append(f"- [{c['name']}]({BASE_URL}/topics/{c['id']}): {len(c['vids'])} videos")
    lines += ["", "## Top 100 videos"]
    for v in cat.ranked[:100]:
        lines.append(f"- [{v['title']}]({BASE_URL}{v['url']}) by {v['channel']}: {v['why_hn']}")
    return "\n".join(lines)


@app.get("/favicon.ico")
@app.get("/favicon.png")
def favicon():
    return FileResponse(os.path.join(HERE, "static", "favicon.png"))


@app.get("/pricing")
def pricing():
    return RedirectResponse("/pro", 301)


INDEXNOW_KEY = "888d9f608ebf739c8aed33aad92cd2ff"


@app.get("/888d9f608ebf739c8aed33aad92cd2ff.txt", response_class=PlainTextResponse)
def indexnow_key():
    return INDEXNOW_KEY


@app.get("/status", response_class=PlainTextResponse)
@app.get("/healthz", response_class=PlainTextResponse)
def healthz():
    return f"ok release={datarelease.current()} videos={len(cat.videos)}"


# ---------------- auth ----------------
def oauth_redirect(request: Request, scopes: str, nxt: str, yt: bool, hint: str = ""):
    if not GOOGLE_CLIENT_ID:
        return RedirectResponse("/pro?msg=signin-unavailable", 302)
    nonce = secrets.token_urlsafe(16)
    state = state_signer.dumps({"n": nonce, "next": safe_next(nxt), "yt": yt})
    params = {"client_id": GOOGLE_CLIENT_ID, "redirect_uri": BASE_URL + "/auth/callback",
              "response_type": "code", "scope": scopes, "state": state,
              "include_granted_scopes": "true", "access_type": "online", "prompt": "select_account"}
    if hint:
        params["login_hint"] = hint
    resp = RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params), 302)
    resp.set_cookie("ht_n", nonce, max_age=600, httponly=True, secure=SECURE, samesite="lax")
    return resp


@app.get("/auth/login")
def login(request: Request, next: str = "/"):
    return oauth_redirect(request, "openid email profile", next, False)


@app.get("/auth/youtube")
def connect_youtube(request: Request, next: str = "/me"):
    u = current_user(request)
    return oauth_redirect(request, f"openid email profile {YT_SCOPE}", next, True,
                          hint=(u or {}).get("email", ""))


@app.get("/auth/callback")
async def callback(request: Request, code: str = "", state: str = "", error: str = ""):
    try:
        st = state_signer.loads(state, max_age=600)
    except BadSignature:
        return RedirectResponse("/?msg=login-expired", 302)
    if error or not code or st.get("n") != request.cookies.get("ht_n"):
        return RedirectResponse(st.get("next", "/") if not error else "/?msg=login-cancelled", 302)
    async with httpx.AsyncClient(timeout=20) as client:
        tok = (await client.post("https://oauth2.googleapis.com/token", data={
            "code": code, "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": BASE_URL + "/auth/callback", "grant_type": "authorization_code"})).json()
        access = tok.get("access_token")
        if not access:
            log.warning("token exchange failed: %s", tok.get("error"))
            return RedirectResponse("/?msg=login-failed", 302)
        info = (await client.get("https://openidconnect.googleapis.com/v1/userinfo",
                                 headers={"Authorization": f"Bearer {access}"})).json()
        uid = info.get("sub")
        if not uid:
            return RedirectResponse("/?msg=login-failed", 302)
        u = store.get(uid)
        store.upsert(uid, {"email": info.get("email", ""), "name": info.get("name", ""),
                           "picture": info.get("picture", ""), "last_login": now()})
        nxt = st.get("next", "/")
        if not u:
            log.info("funnel signup uid=%s", uid)
            if not nxt.startswith("/pro"):
                nxt = "/connect"
        back = "/connect" if nxt.startswith("/connect") else "/me"
        if st.get("yt") and YT_SCOPE in tok.get("scope", ""):
            try:
                await import_youtube(client, access, uid)
                nxt = back + "?msg=youtube-connected"
            except Exception as e:  # noqa: BLE001
                log.exception("youtube import failed")
                nxt = back + "?msg=youtube-failed"
        elif st.get("yt"):
            nxt = back + "?msg=youtube-not-granted"
    resp = RedirectResponse(nxt, 302)
    resp.set_cookie("ht_s", signer.dumps({"uid": uid}), max_age=60 * 60 * 24 * 90, httponly=True,
                    secure=SECURE, samesite="lax")
    resp.delete_cookie("ht_n")
    return resp


async def yt_pages(client, url, params, access, max_pages=4):
    items, token = [], None
    for _ in range(max_pages):
        p = dict(params, maxResults=50)
        if token:
            p["pageToken"] = token
        r = await client.get(url, params=p, headers={"Authorization": f"Bearer {access}"})
        if r.status_code != 200:
            log.info("yt %s -> %s", url.rsplit("/", 1)[-1], r.status_code)
            break
        d = r.json()
        items += d.get("items", [])
        token = d.get("nextPageToken")
        if not token:
            break
    return items


async def import_youtube(client, access, uid):
    likes = await yt_pages(client, "https://www.googleapis.com/youtube/v3/videos",
                           {"part": "snippet", "myRating": "like"}, access)
    subs = await yt_pages(client, "https://www.googleapis.com/youtube/v3/subscriptions",
                          {"part": "snippet", "mine": "true"}, access)
    liked_ids = [it["id"] for it in likes]
    in_cat = [v for v in liked_ids if v in cat.by_vid]
    texts = [it["snippet"]["title"] for it in likes if it["id"] not in cat.by_vid]
    chan_ids = [it["snippet"]["resourceId"]["channelId"] for it in subs]
    texts += [f"{it['snippet']['title']}. {it['snippet'].get('description', '')[:160]}" for it in subs]
    matched = [c for c in chan_ids if c in cat.channel_ids]
    mine = await yt_pages(client, "https://www.googleapis.com/youtube/v3/channels", {"part": "id", "mine": "true"}, access, 1)
    u = store.get(uid) or {}
    u["uid"] = uid
    vec = await asyncio.to_thread(cat.text_vector, texts)
    sources, vecs = dict(u.get("sources") or {}), dict(u.get("vecs") or {})
    sources["youtube"] = {"connected_at": now()}
    vecs["youtube"] = vec
    save_profile(u, {"yt_connected_at": now(), "yt_liked_total": len(liked_ids),
                     "yt_liked_in_catalog": in_cat, "yt_subs_total": len(chan_ids),
                     "yt_channels_matched": matched, "sources": sources, "vecs": vecs,
                     "yt_channel_id": (mine[0].get("id") or "") if mine else ""})
    log.info("funnel youtube-connected uid=%s likes=%d subs=%d", uid, len(liked_ids), len(chan_ids))


@app.get("/auth/logout")
def logout():
    resp = RedirectResponse("/", 302)
    resp.delete_cookie("ht_s")
    return resp


# ---------------- user API ----------------
@app.post("/api/toggle")
async def toggle(request: Request):
    u = current_user(request)
    if not u:
        return JSONResponse({"ok": False, "login": True}, 401)
    body = await request.json()
    kind, vid, on = body.get("kind"), body.get("vid"), bool(body.get("on"))
    if kind not in ("likes", "later") or vid not in cat.by_vid:
        return JSONResponse({"ok": False}, 400)
    lst = [x for x in u.get(kind, []) if x != vid]
    if on:
        lst.append(vid)
    if kind == "likes" and (u.get("vec") or u.get("vecs")):
        save_profile(u, {kind: lst[-2000:]})
    else:
        store.upsert(u["uid"], {kind: lst[-2000:]})
    return {"ok": True}


@app.post("/api/merge")
async def merge(request: Request):
    u = current_user(request)
    if not u:
        return JSONResponse({"ok": False}, 401)
    body = await request.json()
    upd = {}
    for kind in ("likes", "later"):
        add = [v for v in body.get(kind, []) if v in cat.by_vid]
        cur = list(u.get(kind, []))
        cur += [v for v in add if v not in cur]
        upd[kind] = cur[-2000:]
    store.upsert(u["uid"], upd)
    return {"ok": True, **upd}


VID_RE = re.compile(r"watch\?v(?:=|\\u003d)([A-Za-z0-9_-]{11})")


def parse_history(raw: bytes, name: str):
    blobs = []
    if name.lower().endswith(".zip") or raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            for n in z.namelist():
                if "watch-history" in n.lower():
                    blobs.append(z.read(n))
    else:
        blobs.append(raw)
    vids, titles = [], []
    for b in blobs:
        text = b.decode("utf-8", "ignore")
        try:
            for item in json.loads(text):
                m = VID_RE.search(item.get("titleUrl", ""))
                if m:
                    vids.append(m.group(1))
                    titles.append(re.sub(r"^Watched ", "", item.get("title", "")))
        except Exception:
            vids += VID_RE.findall(text)
    return vids, titles


@app.post("/me/import-history")
async def import_history(request: Request, file: UploadFile = File(...), next: str = Form("/me")):
    u = current_user(request)
    if not u:
        return RedirectResponse("/auth/login?next=/me", 303)
    back = "/connect" if safe_next(next).startswith("/connect") else "/me"
    raw = await file.read(60 * 1024 * 1024)
    try:
        vids, titles = parse_history(raw, file.filename or "")
    except Exception:
        return RedirectResponse(back + "?msg=history-unreadable", 303)
    if not vids:
        return RedirectResponse(back + "?msg=history-empty", 303)
    in_cat = sorted({v for v in vids if v in cat.by_vid})
    uniq_titles = list(dict.fromkeys(t for t in titles if t))[:250]
    vec = await asyncio.to_thread(cat.text_vector, uniq_titles or [cat.by_vid[v]["title"] for v in in_cat[:50]])
    sources, vecs = dict(u.get("sources") or {}), dict(u.get("vecs") or {})
    sources["history"] = {"connected_at": now()}
    vecs["history"] = vec
    save_profile(u, {"hist_total": len(set(vids)), "hist_in_catalog": in_cat, "hist_imported_at": now(),
                     "sources": sources, "vecs": vecs})
    log.info("funnel history-imported uid=%s total=%d in_cat=%d", u["uid"], len(set(vids)), len(in_cat))
    return RedirectResponse(back + "?msg=history-imported", 303)


@app.post("/me/delete-data")
def delete_data(request: Request):
    u = current_user(request)
    if not u:
        return RedirectResponse("/", 303)
    for k in ("vec", "vecs", "sources", "interests", "yt_liked_in_catalog", "yt_channels_matched",
              "yt_liked_total", "yt_subs_total", "yt_connected_at", "hist_in_catalog", "hist_total",
              "hist_imported_at"):
        u.pop(k, None)
    store.replace(u["uid"], {k: v for k, v in u.items() if k != "uid"})
    return RedirectResponse("/me?msg=data-deleted", 303)


# ---------------- payments ----------------
@app.post("/pro/checkout")
def checkout(request: Request):
    u = current_user(request)
    if not u:
        return RedirectResponse("/auth/login?next=/pro", 303)
    if u.get("pro"):
        return RedirectResponse("/me", 303)
    s = stripe.checkout.Session.create(
        mode="payment", line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
        client_reference_id=u["uid"], customer_email=u.get("email") or None,
        metadata={"app": "hackertube", "uid": u["uid"]},
        payment_intent_data={"metadata": {"app": "hackertube", "uid": u["uid"]},
                             "description": "HackerTube AI Pro"},
        allow_promotion_codes=True,
        success_url=BASE_URL + "/pro/success?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=BASE_URL + "/pro?cancelled=1")
    log.info("funnel checkout-started uid=%s", u["uid"])
    return RedirectResponse(s.url, 303)


def grant_pro(uid, d):
    session_id = d["id"]
    was_pro = bool((store.get(uid) or {}).get("pro"))
    store.upsert(uid, {"pro": True, "pro_since": now(), "stripe_session": session_id})
    log.info("funnel pro-granted uid=%s", uid)
    if not was_pro:  # success page and webhook both call this; count the purchase once
        track_server("pro_purchased", uid, {"value": 5, "currency": "USD", "stripe_session": session_id})


@app.get("/pro/success")
def success(request: Request, session_id: str = ""):
    u = current_user(request)
    try:
        s = stripe.checkout.Session.retrieve(session_id)
    except Exception:
        return RedirectResponse("/pro", 302)
    d = s.to_dict()  # stripe>=8 objects are not dicts; .get() raises
    uid = d.get("client_reference_id")
    if d.get("payment_status") == "paid" and uid and (d.get("metadata") or {}).get("app") == "hackertube":
        grant_pro(uid, d)
    nxt = "/me?msg=pro-welcome" if u else "/auth/login?next=/me"
    return RedirectResponse(nxt, 302)


@app.post("/stripe/webhook")
async def webhook(request: Request):
    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(payload, request.headers.get("stripe-signature", ""),
                                               STRIPE_WEBHOOK_SECRET)
    except Exception:
        return JSONResponse({"ok": False}, 400)
    if event["type"] == "checkout.session.completed":
        s = event["data"]["object"].to_dict()  # other apps on this Stripe account post here too; ignore them
        if (s.get("metadata") or {}).get("app") == "hackertube" and s.get("payment_status") == "paid":
            grant_pro(s["client_reference_id"], s)
    return {"ok": True}


@app.exception_handler(404)
async def not_found(request: Request, exc):
    return page(request, "404.html", {"title": "Not found", "description": ""}, 404)


# ---------------- connect page and sources ----------------
async def run_email_lookup(u):
    async with httpx.AsyncClient(timeout=10) as client:
        found = await src.email_lookup(client, u.get("email", ""))
    sources, vecs = dict(u.get("sources") or {}), dict(u.get("vecs") or {})
    sources["email"] = {"on": True, "found": found, "answered": False, "connected_at": now()}
    vecs["email"] = (await asyncio.to_thread(cat.text_vector, [found["bio"]])) if found.get("bio") else None
    save_profile(u, {"sources": sources, "vecs": vecs})
    log.info("funnel email-lookup uid=%s github=%s site=%s", u["uid"], bool(found.get("github")),
             bool(found.get("website")))


@app.get("/connect", response_class=HTMLResponse)
async def connect(request: Request):
    u = current_user(request)
    if not u:
        return RedirectResponse("/auth/login?next=/connect", 302)
    if (u.get("sources") or {}).get("email") is None:
        try:
            await run_email_lookup(u)
        except Exception:  # noqa: BLE001
            log.exception("email lookup failed")
    states = source_states(u)
    defs = [dict(d, **states[d["name"]]) for d in SOURCE_DEFS
            if d["name"] != "discord" or src.discord_enabled()]
    lookup = (u.get("sources") or {}).get("email") or {}
    found = lookup.get("found") or {}
    show_found = bool(lookup.get("on", True) and not lookup.get("answered") and (found.get("github") or found.get("website")))
    return page(request, "connect.html", {
        "primary": [d for d in defs if d.get("primary")], "more": [d for d in defs if not d.get("primary")],
        "lookup": lookup, "show_found": show_found, "profile": profile_json(u), "personal": personal_data(u),
        "candidates": PICK_CANDIDATES, "picked": picks(u),
        "title": "Build your HackerTube", "description": "", "noindex": True,
        "msg": request.query_params.get("msg", "")})


@app.get("/connect/feed", response_class=HTMLResponse)
def connect_feed(request: Request):
    u = current_user(request)
    if not u:
        return HTMLResponse("", 401)
    resp = templates.TemplateResponse(request, "_feed.html", {
        "request": request, "user": u, "pro": bool(u.get("pro")), "personal": personal_data(u),
        "likes": u.get("likes", []), "later": u.get("later", [])})
    resp.headers["Cache-Control"] = "private, max-age=0"
    return resp


@app.get("/api/profile")
def api_profile(request: Request):
    u = current_user(request)
    if not u:
        return JSONResponse({"ok": False}, 401)
    return {"ok": True, "profile": profile_json(u)}


@app.post("/api/source/{name}")
async def api_source(name: str, request: Request):
    u = current_user(request)
    if not u:
        return JSONResponse({"ok": False, "error": "Sign in first."}, 401)
    if name not in SOURCE_NAMES:
        return JSONResponse({"ok": False, "error": "Unknown source."}, 400)
    body = await request.json()
    on, handle = bool(body.get("on")), (body.get("handle") or "").strip()[:300]
    sources, vecs = dict(u.get("sources") or {}), dict(u.get("vecs") or {})
    extra = {"sources": sources, "vecs": vecs}
    if not on:
        if name == "youtube":
            extra.update({"yt_connected_at": None, "yt_liked_in_catalog": [], "yt_channels_matched": [],
                          "yt_liked_total": 0, "yt_subs_total": 0})
        elif name == "history":
            extra.update({"hist_imported_at": None, "hist_in_catalog": [], "hist_total": 0})
        elif name == "screenshots":
            extra.update({"shot_in_catalog": []})
        elif name == "aiprofile":
            extra.update({"ai_in_catalog": []})
        elif name == "pastes":
            extra.update({"paste_in_catalog": [], "paste_watched": []})
        sources[name] = {"on": False, "answered": True, "found": {}} if name == "email" else None
        vecs[name] = None
        save_profile(u, extra)
        log.info("funnel source-off uid=%s source=%s", u["uid"], name)
    elif name == "email":
        try:
            await run_email_lookup(u)
        except Exception:  # noqa: BLE001
            log.exception("email lookup failed")
            return JSONResponse({"ok": False, "error": "The lookup didn't answer. Try again in a minute."}, 502)
    elif name in ("youtube", "history", "discord", "screenshots", "aiprofile", "pastes"):
        return JSONResponse({"ok": False, "error": "Use the connect button for this source."}, 400)
    elif name == "picks":
        vids = [v for v in (body.get("vids") or []) if isinstance(v, str) and v in cat.by_vid][:30]
        if len(vids) < 3:
            return JSONResponse({"ok": False, "error": "Pick at least three."}, 400)
        sources["picks"] = {"vids": vids, "label": f"{len(vids)} videos picked", "connected_at": now()}
        save_profile(u, extra)
        log.info("funnel source-connected uid=%s source=picks n=%d", u["uid"], len(vids))
    else:
        if not handle:
            return JSONResponse({"ok": False, "error": "Enter a username or link first."}, 400)
        key = (name, handle.lower())
        hit = SOURCE_CACHE.get(key)
        if hit and datetime.datetime.now().timestamp() - hit[0] < CACHE_TTL:
            res = hit[1]
        else:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    res = await fetch_source(client, name, handle)
            except src.SourceError as e:
                return JSONResponse({"ok": False, "error": str(e)}, 400)
            except (httpx.HTTPError, ValueError, KeyError) as e:
                log.warning("source %s failed: %r", name, e)
                return JSONResponse({"ok": False, "error": "That service didn't answer. Try again in a minute."}, 502)
            if len(SOURCE_CACHE) > 2000:
                SOURCE_CACHE.clear()
            SOURCE_CACHE[key] = (datetime.datetime.now().timestamp(), res)
        await attach_source(u, name, res)
        save_profile(u)
    return {"ok": True, "profile": profile_json(u), "source": source_states(u)[name]}


@app.post("/api/email-lookup")
async def api_email_lookup(request: Request):
    u = current_user(request)
    if not u:
        return JSONResponse({"ok": False, "error": "Sign in first."}, 401)
    body = await request.json()
    use = bool(body.get("use"))
    sources = dict(u.get("sources") or {})
    rec = dict(sources.get("email") or {})
    rec["answered"] = True
    sources["email"] = rec
    u["sources"] = sources
    connected = []
    if use:
        found = rec.get("found") or {}
        async with httpx.AsyncClient(timeout=15) as client:
            for name, handle in (("github", found.get("github")), ("website", found.get("website"))):
                if not handle or ((u.get("sources") or {}).get(name) or {}).get("connected_at"):
                    continue
                try:
                    res = await fetch_source(client, name, handle)
                except Exception as e:  # noqa: BLE001
                    log.info("email-lookup connect %s failed: %r", name, e)
                    continue
                await attach_source(u, name, res)
                connected.append(name)
    save_profile(u)
    return {"ok": True, "connected": connected, "profile": profile_json(u), "sources": source_states(u)}


@app.get("/auth/discord")
def discord_login(request: Request):
    u = current_user(request)
    if not u:
        return RedirectResponse("/auth/login?next=/connect", 302)
    if not src.discord_enabled():
        return RedirectResponse("/connect?msg=discord-unavailable", 302)
    nonce = secrets.token_urlsafe(16)
    state = state_signer.dumps({"n": nonce, "p": "discord"})
    resp = RedirectResponse(src.discord_auth_url(BASE_URL + "/auth/discord/callback", state), 302)
    resp.set_cookie("ht_n", nonce, max_age=600, httponly=True, secure=SECURE, samesite="lax")
    return resp


@app.get("/auth/discord/callback")
async def discord_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    u = current_user(request)
    if not u:
        return RedirectResponse("/auth/login?next=/connect", 302)
    try:
        st = state_signer.loads(state, max_age=600)
    except BadSignature:
        return RedirectResponse("/connect?msg=discord-failed", 302)
    if error or not code or st.get("p") != "discord" or st.get("n") != request.cookies.get("ht_n"):
        return RedirectResponse("/connect?msg=discord-failed", 302)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await src.discord_guilds(client, code, BASE_URL + "/auth/discord/callback")
    except (src.SourceError, httpx.HTTPError):
        return RedirectResponse("/connect?msg=discord-failed", 302)
    await attach_source(u, "discord", res)
    save_profile(u)
    resp = RedirectResponse("/connect?msg=discord-connected", 302)
    resp.delete_cookie("ht_n")
    return resp


if os.environ.get("DEV_LOGIN"):  # local testing only; never set in deploy.sh
    @app.get("/dev/login")
    def dev_login(uid: str = "dev-user", pro: int = 0, email: str = ""):
        store.upsert(uid, {"email": email or f"{uid}@example.com", "name": "Dev User", "pro": bool(pro)})
        resp = RedirectResponse("/connect", 302)
        resp.set_cookie("ht_s", signer.dumps({"uid": uid}), httponly=True, samesite="lax")
        return resp


# ---------------- screenshot import ----------------
SHOT_PER_IP_HOUR = int(os.environ.get("SHOT_PER_IP_HOUR", "12"))
SHOT_DAILY_CAP = int(os.environ.get("SHOT_DAILY_CAP", "2000"))
_shot_hits = {}
_shot_day = {"day": "", "n": 0}


def shot_allowed(ip):
    t = datetime.datetime.now(datetime.timezone.utc)
    day = t.date().isoformat()
    if _shot_day["day"] != day:
        _shot_day.update(day=day, n=0)
    if _shot_day["n"] >= SHOT_DAILY_CAP:
        return False
    hist = [x for x in _shot_hits.get(ip, []) if (t - x).total_seconds() < 3600]
    if len(hist) >= SHOT_PER_IP_HOUR:
        _shot_hits[ip] = hist
        return False
    hist.append(t)
    _shot_hits[ip] = hist
    _shot_day["n"] += 1
    if len(_shot_hits) > 5000:
        _shot_hits.clear()
    return True


def client_ip(request):
    return (request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "?"))


def attach_screenshots(u, res, images):
    sources, vecs = dict(u.get("sources") or {}), dict(u.get("vecs") or {})
    rec = dict(sources.get("screenshots") or {})
    vids = list(dict.fromkeys(list(rec.get("vids") or []) + res["vids"]))
    titles = list(dict.fromkeys(list(rec.get("titles") or []) + res["titles"]))[-300:]
    n_img = int(rec.get("images") or 0) + images
    n_read = int(rec.get("read") or 0) + int(res.get("recognised") or len(res.get("titles") or []))
    rec.update({"vids": vids, "titles": titles, "images": n_img, "read": n_read, "connected_at": now(),
                "label": f"{n_img} screenshot{'s' if n_img != 1 else ''} · {n_read} videos read · {len(vids)} HN picks watched"})
    sources["screenshots"] = rec
    vecs["screenshots"] = cat.text_vector(titles)
    save_profile(u, {"sources": sources, "vecs": vecs, "shot_in_catalog": vids})


def render_shot_results(request, u, res):
    return templates.env.get_template("_shot_results.html").render(
        request=request, r=res, user=u, pro=bool(u and u.get("pro")),
        likes=u.get("likes", []) if u else [], later=u.get("later", []) if u else [])


@app.get("/screenshot", response_class=HTMLResponse)
def screenshot_page(request: Request):
    return page(request, "screenshot.html", {
        "title": "See which Hacker News essentials you've already watched, from one YouTube screenshot",
        "description": "Drop a screenshot of your YouTube history, liked videos or subscriptions. HackerTube AI reads the titles, "
                       "shows which Hacker News favourites you've watched, and picks what to watch next. No account needed.",
        "total": len(cat.videos)})


@app.post("/api/screenshot")
async def api_screenshot(request: Request, files: list[UploadFile] = File(default=[]), sample: str = Form("")):
    u = current_user(request)
    if not shot_allowed(client_ip(request)):
        return JSONResponse({"ok": False, "error": "That's a lot of screenshots for one hour. Try again a bit later."}, 429)
    raws = []
    if sample:
        raws = [open(os.path.join(HERE, "static", "sample-history.png"), "rb").read()]
    else:
        for f in files[:4]:
            raw = await f.read(8 * 1024 * 1024 + 1)
            if len(raw) > 8 * 1024 * 1024:
                return JSONResponse({"ok": False, "error": "Each image needs to be under 8 MB."}, 400)
            if raw:
                raws.append(raw)
    if not raws:
        return JSONResponse({"ok": False, "error": "Add a screenshot first."}, 400)
    try:
        parts = await asyncio.gather(*(asyncio.to_thread(shot.extract, [raw]) for raw in raws))
        extracted = shot.merge_extractions(parts)
    except shot.ScreenshotError as e:
        return JSONResponse({"ok": False, "error": str(e)}, 400)
    except anthropic.APIError as e:
        log.warning("screenshot model error: %r", e)
        return JSONResponse({"ok": False, "error": "The reader is busy right now. Try again in a minute."}, 502)
    res = await asyncio.to_thread(shot.analyse, cat, extracted)
    log.info("funnel screenshot uid=%s images=%d recognised=%d matched=%d confidence=%s closeness=%s sample=%s",
             (u or {}).get("uid", "anon"), len(raws), res["recognised"], len(res["vids"]), res["confidence"],
             res["closeness"], bool(sample))
    out = {"ok": True, "stats": {"recognised": res["recognised"], "matched": len(res["vids"]),
                                 "essentials_seen": res["essentials_seen"], "ess_total": res["ess_total"]},
           "store": {"vids": res["vids"], "titles": res["titles"], "recognised": res["recognised"]}, "interests": res["interests"]}
    if u and not sample:
        attach_screenshots(u, res, len(raws))
        out["profile"] = profile_json(u)
        out["source"] = source_states(u)["screenshots"]
    out["html"] = render_shot_results(request, u, res)
    return out


@app.post("/api/screenshot/merge")
async def api_screenshot_merge(request: Request):
    u = current_user(request)
    if not u:
        return JSONResponse({"ok": False}, 401)
    body = await request.json()
    vids = [v for v in (body.get("vids") or []) if isinstance(v, str) and v in cat.by_vid][:500]
    titles = [str(t)[:200] for t in (body.get("titles") or []) if isinstance(t, str) and t.strip()][:300]
    if not vids and not titles:
        return {"ok": True, "profile": profile_json(u)}
    attach_screenshots(u, {"vids": vids, "titles": titles, "recognised": int(body.get("recognised") or 0)}, 1)
    log.info("funnel screenshot-merged uid=%s vids=%d titles=%d", u["uid"], len(vids), len(titles))
    return {"ok": True, "profile": profile_json(u)}


# ---------------- "Ask your AI" onboarding ----------------
def attach_ai_profile(u, res):
    prof = res["profile"]
    sources, vecs = dict(u.get("sources") or {}), dict(u.get("vecs") or {})
    sources["aiprofile"] = {"connected_at": now(), "profile": prof,
                            "label": f"{len(prof.get('topics') or [])} topics · {len(res.get('creators_found') or [])} creators matched"}
    vecs["aiprofile"] = cat.text_vector(res["texts"])
    save_profile(u, {"sources": sources, "vecs": vecs, "ai_in_catalog": list(res["vids"])[:60]})


@app.get("/ask-your-ai", response_class=HTMLResponse)
def ask_page(request: Request):
    return page(request, "ask.html", {
        "title": "Ask your AI what you'd enjoy: personalised talk recommendations from ChatGPT, Claude or Gemini memory",
        "description": "Copy one prompt into ChatGPT, Claude or Gemini, paste the answer back, and get the Hacker News recommended talks "
                       "that fit what you actually work on and want to learn. No account needed.",
        "prompt": aip.PROMPT_FOR_USER, "prompt_q": urllib.parse.quote(aip.PROMPT_FOR_USER)})


@app.post("/api/ai-profile")
async def api_ai_profile(request: Request):
    u = current_user(request)
    if not shot_allowed(client_ip(request)):
        return JSONResponse({"ok": False, "error": "That's a lot of tries for one hour. Try again a bit later."}, 429)
    body = await request.json()
    text = str(body.get("text") or "")[:20000]
    try:
        prof = await asyncio.to_thread(aip.parse, text)
    except aip.ProfileError as e:
        return JSONResponse({"ok": False, "error": str(e)}, 400)
    except anthropic.APIError as e:
        log.warning("ai profile model error: %r", e)
        return JSONResponse({"ok": False, "error": "The reader is busy right now. Try again in a minute."}, 502)
    res = await asyncio.to_thread(aip.analyse, cat, prof)
    log.info("funnel ai-profile uid=%s topics=%d creators=%d anchors=%d confidence=%s closeness=%s",
             (u or {}).get("uid", "anon"), len(prof["topics"]), len(res["creators_found"]), len(res["vids"]),
             res["confidence"], res["closeness"])
    out = {"ok": True, "interests": res["interests"],
           "store": {"profile": prof, "vids": res["vids"], "texts": res["texts"], "creators_found": res["creators_found"]}}
    if u:
        attach_ai_profile(u, res)
        out["profile"] = profile_json(u)
    out["html"] = templates.env.get_template("_ai_results.html").render(
        request=request, r=res, user=u, pro=bool(u and u.get("pro")), ess_total=len(cat.essentials),
        likes=u.get("likes", []) if u else [], later=u.get("later", []) if u else [])
    return out


@app.post("/api/ai-profile/merge")
async def api_ai_profile_merge(request: Request):
    u = current_user(request)
    if not u:
        return JSONResponse({"ok": False}, 401)
    body = await request.json()
    prof = body.get("profile") or {}
    if not isinstance(prof, dict):
        return JSONResponse({"ok": False}, 400)
    prof = {k: [str(x)[:120] for x in (prof.get(k) or []) if isinstance(x, str)][:15]
            for k in ("topics", "work", "learning", "creators", "titles", "suggestions")} | {
        "format": str(prof.get("format") or "unknown")[:10], "level": str(prof.get("level") or "unknown")[:15]}
    res = {"profile": prof, "vids": [v for v in (body.get("vids") or []) if isinstance(v, str) and v in cat.by_vid][:60],
           "texts": [str(t)[:200] for t in (body.get("texts") or []) if isinstance(t, str)][:200],
           "creators_found": [c for c in (body.get("creators_found") or []) if isinstance(c, dict)][:20]}
    if not res["texts"]:
        return {"ok": True}
    attach_ai_profile(u, res)
    log.info("funnel ai-profile-merged uid=%s", u["uid"])
    return {"ok": True, "profile": profile_json(u)}


# ---------------- communities and reports ----------------
@app.get("/c/{slug}", response_class=HTMLResponse)
def community_page(request: Request, slug: str, area: str = ""):
    c = cat.comm_by_slug.get(slug)
    if not c:
        return page(request, "404.html", {"title": "Not found", "description": ""}, 404)
    filters = []
    if c["id"] == WEB_COMM:  # the open web counts linking sites across monthly crawls, not people
        counts = cat.web_counts  # order and counts are computed once per catalogue load
        area = area if area in cat.area_by_id and counts.get(area) else ""
        filters = [{"href": f"/c/{slug}", "label": "All fields", "n": len(cat.web_order), "on": not area}] + [
            {"href": f"/c/{slug}?area={a}", "label": AREA_SHORT.get(a, cat.area_by_id[a]["name"]), "n": n, "on": a == area}
            for a, n in counts.most_common() if a in cat.area_by_id]
        vids = cat.web_by_area.get(area, []) if area else cat.web_order
        field = f" in {AREA_SHORT.get(area, area).lower()}" if area else ""
        heading, intro = "The YouTube videos the open web keeps linking" + (f": {AREA_SHORT.get(area, area)}" if area else ""), (
            f"{len(vids)} talks, lectures and explainers{field} over three minutes that at least 2 independent, widely read websites "
            "link to, found by crawling the open web and ranked by how many reputable sites cite them.")
    else:
        vids = sorted((cat.by_vid[x] for x in c["vids"]), key=lambda v: -((v.get("comm") or {}).get(c["id"], {}).get("score", v["score"])))
        heading = f"The YouTube videos {c['label']} keeps recommending"
        intro = f"{len(vids)} videos that at least 3 different people on {c['label']} recommended, in at least 3 different months."
    return page(request, "list.html", {
        "heading": heading, "intro": intro, "filters": filters,
        "videos": vids[:200], "title": f"Best YouTube videos recommended by {c['label']}" + (f": {AREA_SHORT.get(area, area)}" if area else ""),
        "description": f"The talks, lectures and explainers {c['label']} keeps pointing people to: "
                       + ", ".join(v["title"] for v in vids[:3])[:220]})


@app.post("/api/report")
async def api_report(request: Request):
    body = await request.json()
    vid, reason = str(body.get("vid") or "")[:20], str(body.get("reason") or "")[:500]
    if vid not in cat.by_vid or not reason.strip():
        return JSONResponse({"ok": False}, 400)
    if not shot_allowed(client_ip(request)):
        return JSONResponse({"ok": False}, 429)
    u = current_user(request)
    log.warning("report vid=%s uid=%s reason=%r", vid, (u or {}).get("uid", "anon"), reason)
    try:
        if hasattr(store, "db"):
            store.db.collection("ht_reports").add({"vid": vid, "reason": reason, "uid": (u or {}).get("uid"), "at": now()})
    except Exception:  # noqa: BLE001
        log.exception("report store failed")
    return {"ok": True}


@app.get("/communities", response_class=HTMLResponse)
def communities_page(request: Request):
    comms = sorted(cat.comms.values(), key=lambda c: -len(c["vids"]))
    return page(request, "communities.html", {
        "comms": comms, "title": "Communities: YouTube videos recommended by Hacker News, Stack Overflow and Stack Exchange",
        "description": f"Browse videos by the community that vouched for them: {len(comms)} communities, from Hacker News and "
                       "Stack Overflow to Physics, Blender, Aviation and Worldbuilding Stack Exchange."})


# ---------------- home page feed and paste-anything ----------------
@app.get("/api/feed", response_class=HTMLResponse)
def api_feed(request: Request, areas: str = "", c: str = ""):
    a = [x for x in areas.split(",") if x in cat.area_by_id][:6]
    cm = [x for x in c.split(",") if x in cat.comms][:3]
    rows, n = cat.feed_rows(a, cm, seed="chips" + areas + c)
    label = ", ".join([AREA_SHORT.get(x, x) for x in a] + [cat.comms[x]["label"] for x in cm])
    resp = templates.TemplateResponse(request, "_feed_rows.html", {"request": request, "rows": rows, "scope_label": label,
                                                                    "pool_n": n, "likes": [], "later": []})
    resp.headers["Cache-Control"] = "public, max-age=600"
    return resp


def load_prior(raw):
    try:
        items = (json.loads(raw) if raw else {}).get("items") or []
    except json.JSONDecodeError:
        return []
    out = []
    for it in items[:20]:
        if isinstance(it, dict):
            out.append({"label": str(it.get("label", ""))[:80], "kind": str(it.get("kind", ""))[:20],
                        "vids": [v for v in (it.get("vids") or []) if isinstance(v, str) and v in cat.by_vid][:60],
                        "watched": [v for v in (it.get("watched") or []) if isinstance(v, str) and v in cat.by_vid][:300],
                        "texts": [str(t)[:200] for t in (it.get("texts") or []) if isinstance(t, str)][:150],
                        "neg": [v for v in (it.get("neg") or []) if isinstance(v, str) and v in cat.by_vid][:200]})
    return out


def attach_pastes(u, res):
    sources, vecs = dict(u.get("sources") or {}), dict(u.get("vecs") or {})
    items = res["store"]["items"]
    vids = list(dict.fromkeys(v for it in items for v in it["vids"]))
    watched = list(dict.fromkeys(v for it in items for v in it.get("watched", [])))
    texts = [t for it in items for t in it["texts"]][-300:]
    sources["pastes"] = {"connected_at": now(), "items": [{"label": it["label"], "kind": it["kind"]} for it in items],
                         "label": f"{len(items)} thing{'s' if len(items) != 1 else ''} pasted · {len(vids)} catalogue matches"}
    vecs["pastes"] = cat.text_vector(texts) if texts else None
    save_profile(u, {"sources": sources, "vecs": vecs, "paste_in_catalog": vids[:200], "paste_watched": watched[:500]})


@app.post("/api/anything")
async def api_anything(request: Request, files: list[UploadFile] = File(default=[]), text: str = Form(""), prior: str = Form("")):
    u = current_user(request)
    text = (text or "").strip()[:20000]
    raws = []
    for f in files[:4]:
        raw = await f.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            return JSONResponse({"ok": False, "error": "Each image needs to be under 8 MB."}, 400)
        if raw:
            raws.append(raw)
    items = anyx.detect(text)
    prior_items = load_prior(prior)
    if not items and not raws and not prior_items:
        return JSONResponse({"ok": False, "error": "Paste a link, a few words, a username, or drop a screenshot."}, 400)
    needs_model = bool(raws) or any(k == "profile_text" for k, _ in items)
    if needs_model and not shot_allowed(client_ip(request)):
        return JSONResponse({"ok": False, "error": "That's a lot of tries for one hour. Try again a bit later."}, 429)
    results, errors = prior_items, []
    async with httpx.AsyncClient(timeout=20) as client:
        async def run(kind, val):
            try:
                return await anyx.read_item(client, cat, kind, val)
            except (anyx.AnythingError, src.SourceError, aip.ProfileError, shot.ScreenshotError) as e:
                errors.append(str(e))
            except Exception as e:  # noqa: BLE001
                log.warning("anything %s failed: %r", kind, e)
                errors.append("One item couldn't be read. Try a different link.")
            return None
        tasks = [run(k, v) for k, v in items[:8]]
        if raws:
            tasks.append(anyx.read_images(cat, raws))
        done = await asyncio.gather(*tasks)
    new_items = [d for d in done if d]
    if not new_items and errors:
        return JSONResponse({"ok": False, "error": errors[0]}, 400)
    results += new_items
    res = await asyncio.to_thread(anyx.analyse, cat, results)
    if not res:
        return JSONResponse({"ok": False, "error": "Nothing we could use in that. Try a link or a few words about what you like."}, 400)
    log.info("funnel anything uid=%s kinds=%s anchors=%d confidence=%s", (u or {}).get("uid", "anon"),
             [i["kind"] for i in res["chips"]], len(res["anchors"]), res["confidence"])
    if u:
        attach_pastes(u, res)
    html = templates.env.get_template("_anything_results.html").render(
        request=request, r=res, user=u, pro=bool(u and u.get("pro")), errors=errors,
        likes=u.get("likes", []) if u else [], later=u.get("later", []) if u else [])
    out = {"ok": True, "html": html, "store": res["store"], "interests": res["interests"], "errors": errors}
    if u:
        out["profile"] = profile_json(u)
    return out


@app.post("/api/anything/merge")
async def api_anything_merge(request: Request):
    u = current_user(request)
    if not u:
        return JSONResponse({"ok": False}, 401)
    items = load_prior(await request.body())
    if not items:
        return {"ok": True}
    res = await asyncio.to_thread(anyx.analyse, cat, items)
    if res:
        attach_pastes(u, res)
        log.info("funnel anything-merged uid=%s items=%d", u["uid"], len(items))
    return {"ok": True, "profile": profile_json(u)}


@app.post("/api/ping")
async def api_ping(request: Request):
    try:
        d = json.loads((await request.body()) or b"{}")
        log.info("funnel %s area=%s", str(d.get("ev", "ping"))[:20], str(d.get("a", ""))[:40])
    except Exception:  # noqa: BLE001
        pass
    return PlainTextResponse("", 204)


# ---------------- phase 2: learning from the player ----------------
def drop_rejected(rows, u):
    """Signed-in users never see what they hid or marked seen in the generic rows."""
    skip = set((u or {}).get("hidden") or []) | set((u or {}).get("seen") or [])
    if not skip:
        return rows
    return [(t, sub, [v for v in vids if v["vid"] not in skip]) for t, sub, vids in rows]


def continue_row(u, limit=8):
    watch = (u or {}).get("watch") or {}
    skip = set(u.get("hidden") or []) | set(u.get("seen") or [])
    rows = [(v, w) for v, w in watch.items() if v in cat.by_vid and v not in skip and 0.05 < (w.get("pct") or 0) < 0.9 and (w.get("pos") or 0) > 30]
    rows.sort(key=lambda x: -(x[1].get("t") or 0))
    out = []
    for v, w in rows[:limit]:
        d = dict(cat.by_vid[v])
        d["resume"] = int(w["pos"])
        d["pct"] = int(100 * w["pct"])
        out.append(d)
    return out


def clean_watch(raw):
    """Validate a watch map from the client: {vid: {pos, dur, pct, secs, t}}."""
    out = {}
    if not isinstance(raw, dict):
        return out
    for vid, w in list(raw.items())[:500]:
        if vid in cat.by_vid and isinstance(w, dict):
            try:
                pos, dur = max(0.0, float(w.get("pos") or 0)), max(0.0, float(w.get("dur") or 0))
                pct = min(1.0, max(0.0, float(w.get("pct") or (pos / dur if dur else 0))))
                out[vid] = {"pos": round(pos, 1), "dur": round(dur, 1), "pct": round(pct, 3),
                            "secs": round(min(float(w.get("secs") or 0), 86400), 1), "t": int(w.get("t") or 0)}
            except (TypeError, ValueError):
                continue
    return out


@app.post("/api/watch")
async def api_watch(request: Request):
    """Beacon from the player: progress for one video. Signed-in users get it stored; everyone else keeps it in the browser."""
    try:
        d = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        return PlainTextResponse("", 204)
    u = current_user(request)
    vid = str(d.get("vid") or "")
    if not u or vid not in cat.by_vid:
        return PlainTextResponse("", 204)
    w = clean_watch({vid: d}).get(vid)
    if not w:
        return PlainTextResponse("", 204)
    watch = dict(u.get("watch") or {})
    prev = watch.get(vid) or {}
    w["pct"] = max(w["pct"], prev.get("pct") or 0)
    w["secs"] = max(w["secs"], prev.get("secs") or 0)
    w["t"] = int(datetime.datetime.now(datetime.timezone.utc).timestamp())
    watch[vid] = w
    if len(watch) > 500:
        for k in sorted(watch, key=lambda k: watch[k].get("t") or 0)[:len(watch) - 500]:
            watch.pop(k)
    crossed = (prev.get("pct") or 0) < WATCHED_PCT <= w["pct"] or (prev.get("pct") or 0) < 0.9 <= w["pct"]
    was_abandoned = (prev.get("pct") or 0) < ABANDON_PCT and (prev.get("secs") or 0) >= ABANDON_SECS
    abandoned = bool(d.get("left")) and w["pct"] < ABANDON_PCT and w["secs"] >= ABANDON_SECS and not was_abandoned
    if crossed or abandoned:
        save_profile(u, {"watch": watch})
        log.info("funnel watch uid=%s vid=%s pct=%.2f %s", u["uid"], vid, w["pct"], "abandoned" if abandoned else "milestone")
    else:
        store.upsert(u["uid"], {"watch": watch})
    return PlainTextResponse("", 204)


@app.post("/api/feedback")
async def api_feedback(request: Request):
    """'Seen it' and 'Not for me' on a card."""
    u = current_user(request)
    if not u:
        return JSONResponse({"ok": False, "login": True}, 401)
    body = await request.json()
    kind, vid, on = body.get("kind"), body.get("vid"), bool(body.get("on", True))
    if kind not in ("seen", "hidden") or vid not in cat.by_vid:
        return JSONResponse({"ok": False}, 400)
    lst = [x for x in (u.get(kind) or []) if x != vid]
    if on:
        lst.append(vid)
    save_profile(u, {kind: lst[-1000:]})
    log.info("funnel feedback uid=%s kind=%s on=%s", u["uid"], kind, on)
    return {"ok": True, "seen_ess": sum(1 for v in cat.essentials if v in watched_set(u))}


@app.post("/api/because", response_class=HTMLResponse)
async def api_because(request: Request):
    """'Because you opened X': videos close to the last one this browser opened, minus what it opened, hid or saw."""
    try:
        d = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        return HTMLResponse("")
    if not isinstance(d, dict) or not isinstance(d.get("opened"), list):
        return HTMLResponse("")
    opened = [v for v in d["opened"] if isinstance(v, str) and v in cat.by_vid][:30]
    if not opened:
        return HTMLResponse("")
    skip = set(opened) | {v for v in (d.get("hidden") or []) if isinstance(v, str)} | {v for v in (d.get("seen") or []) if isinstance(v, str)}
    anchor = cat.by_vid[opened[0]]
    vids = [v for v in cat.similar(anchor["vid"], 40) if v["vid"] not in skip and v["shelf"] != "news"][:8]
    if len(vids) < 4:
        return HTMLResponse("")
    return templates.env.get_template("_because.html").render(
        request=request, anchor=anchor, videos=vids, reasons={v["vid"]: cat.proof(v) for v in vids}, likes=[], later=[])


@app.post("/api/continue", response_class=HTMLResponse)
async def api_continue(request: Request):
    """Anonymous 'Continue watching' row, rendered from the browser's own watch map."""
    try:
        d = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        return HTMLResponse("")
    if not isinstance(d, dict):
        return HTMLResponse("")
    if "watch" in d and isinstance(d.get("watch"), dict):  # {watch, hidden, seen}
        watch, hidden, seen = clean_watch(d["watch"]), d.get("hidden") or [], d.get("seen") or []
    else:  # bare watch map
        watch, hidden, seen = clean_watch(d), [], []
    row = continue_row({"watch": watch, "hidden": [v for v in hidden if isinstance(v, str)][:1000], "seen": [v for v in seen if isinstance(v, str)][:1000]})
    if not row:
        return HTMLResponse("")
    return templates.env.get_template("_continue.html").render(request=request, videos=row, likes=[], later=[])


@app.post("/api/signals/merge")
async def api_signals_merge(request: Request):
    """On sign-in: fold the browser's watch, seen and hidden lists into the account."""
    u = current_user(request)
    if not u:
        return JSONResponse({"ok": False}, 401)
    try:
        d = json.loads((await request.body()) or b"{}")
    except json.JSONDecodeError:
        return JSONResponse({"ok": False}, 400)
    watch = dict(u.get("watch") or {})
    for vid, w in clean_watch(d.get("watch")).items():
        prev = watch.get(vid) or {}
        if (w.get("pct") or 0) >= (prev.get("pct") or 0):
            watch[vid] = w
    seen = list(dict.fromkeys(list(u.get("seen") or []) + [v for v in (d.get("seen") or []) if isinstance(v, str) and v in cat.by_vid]))
    hidden = list(dict.fromkeys(list(u.get("hidden") or []) + [v for v in (d.get("hidden") or []) if isinstance(v, str) and v in cat.by_vid]))
    save_profile(u, {"watch": watch, "seen": seen[-1000:], "hidden": hidden[-1000:]})
    log.info("funnel signals-merged uid=%s watch=%d seen=%d hidden=%d", u["uid"], len(watch), len(seen), len(hidden))
    return {"ok": True, "profile": profile_json(u)}


# ---------------- verdicts, notes, reader judgments and Hackers ----------------
# Unpaid: verdicts and judgments need a watch the server verified. Paid: a Hacker's review never needs a watch
# here, and it earns only from the judgments of readers who watched. Nothing anyone is paid for depends on
# their own playback (YouTube API developer policies III.F.3).
HACKER_AUTO_APPROVE = os.environ.get("HACKER_AUTO_APPROVE", "") == "1"   # anyone can join; admins can switch anyone off
ADMIN_UIDS = {x.strip() for x in os.environ.get("ADMIN_UIDS", "").split(",") if x.strip()}
WATCH_SCALE = min(1.0, max(0.01, float(os.environ.get("WATCH_SCALE", "1") or 1)))  # below 1 only in the test environment
_note_hits = {}
_names = {}  # uid -> (display name, time), so lists don't read every writer's account each time


def notes_ok():
    return NOTES_ON


def need_dur(v):
    return max(1, int((v.get("dur") or 0) * WATCH_SCALE))


def pilot():
    """Which videos' reviews earn: a topic, or * for every video. No pay unless PILOT_TOPIC is set."""
    if not PILOT_TOPIC or (PILOT_TOPIC != "*" and PILOT_TOPIC not in cat.cats):
        return None
    every = PILOT_TOPIC == "*"
    return {"topic": PILOT_TOPIC, "name": "every video" if every else cat.cats[PILOT_TOPIC]["name"],
            "currency": nts.CURRENCY, "every": every, "vids": None if every else set(cat.cats[PILOT_TOPIC]["vids"])}


def pilot_on():
    """Cheap check for every page: is a paid pilot running? Only then does the site mention earning."""
    return NOTES_ON and bool(PILOT_TOPIC) and (PILOT_TOPIC == "*" or PILOT_TOPIC in cat.cats)


templates.env.globals["EARN_ON"] = pilot_on


def waiting_videos(limit=12, within=None):
    """Videos with a partner's paid review still waiting for viewers to rate it, oldest first. The row that
    shows them sends viewers where ratings are needed, without showing the review before they answer."""
    if not pilot_on():
        return []
    oldest = {}
    for a in all_assessed().values():
        if a["paid"] and a["status"] == "waiting" and (within is None or a["vid"] in within):
            oldest[a["vid"]] = min(oldest.get(a["vid"], a["ts"]), a["ts"])
    vids = (cat.get(v) for v, _ in sorted(oldest.items(), key=lambda kv: kv[1]))
    return [v for v in vids if v][:limit]


def in_pilot(pl, vid):
    if not pl:
        return False
    if pl["vids"] is None or vid in pl["vids"]:
        return True
    v = ADDED.get(vid)
    return bool(v) and pl["topic"] in v["cats"]


def hacker_status(u):
    return ((u or {}).get("hacker") or {}).get("status", "none")


def is_admin(u):
    return bool(u and u.get("uid") in ADMIN_UIDS)


def acting(request, u, did):
    """(who, uid, did_h, net_h): the account if signed in, otherwise this browser."""
    did = did if isinstance(did, str) and nts.DID_RE.match(did) else ""
    did_h = ids.device(did) if did else ""
    uid = u["uid"] if u else None
    who = uid or (("d" + did_h[:16]) if did_h else "")
    return who, uid, did_h, ids.network(client_ip(request))


def over_limit(kind, mine, net_h):
    per_person, per_net = nts.DAY_LIMITS[kind]
    now_t = nts.clock()
    if sum(1 for d in mine if now_t - float(d.get("ts", 0)) < 86400) >= per_person:
        return True
    key = (kind, net_h)
    hist = [x for x in _note_hits.get(key, []) if now_t - x < 86400]
    if len(hist) >= per_net:
        _note_hits[key] = hist
        return True
    _note_hits[key] = hist + [now_t]
    return False


def display_name(uid):
    hit = _names.get(uid)
    if hit and nts.clock() - hit[1] < 600:
        return hit[0]
    u = store.get(uid) or {}
    name = u.get("rec_name") or f"Reviewer {ids.handle(uid)}"
    _names[uid] = (name, nts.clock())
    return name


def watch_state(wt, vid):
    """The verified watch behind a token, or None when there is no valid token for this video."""
    try:
        st = watcher.load(str(wt or ""))
    except BadSignature:
        return None
    return st if st.get("v") == vid and nts.fresh(st) else None


def video_notes(vid):
    ns = nstore.notes.by("vid", vid)
    js = nstore.judgments.by("vid", vid)
    return ns, js, nts.assess(ns, js)


def retry_pending(ns, title):
    """Notes whose automatic check failed get checked again, a few at a time, off the request path."""
    todo = [n for n in ns if n.get("mod") == "pending" and nts.clock() - float(n.get("ts", 0)) > 60][:3]
    def run():
        for n in todo:
            mod, why = nts.moderate(n.get("text", ""), "note", title)
            if mod != "pending":
                nstore.notes.update(n["id"], {"mod": mod, "mod_why": why})
    if todo:
        threading.Thread(target=run, daemon=True).start()


def note_view(n, a, who):
    return {"id": n["id"], "vid": n["vid"], "text": n.get("text", ""), "stance": n.get("stance"), "when": nts.ago(n["ts"]),
            "name": display_name(n["uid"]) if n.get("uid") else "A visitor", "handle": n.get("handle") or "",
            "mine": n.get("who") == who, "mod": n.get("mod"), "mod_why": n.get("mod_why", ""),
            "needs": n.get("needs", ""), "moment": n.get("moment"), "moment_s": nts.fmt_moment(n.get("moment")),
            "paid": bool(n.get("paid")), "review": bool(n.get("review")), "own": bool(n.get("own")),
            "agree": (a or {}).get("agree", 0), "judged": (a or {}).get("judged", 0), "status": (a or {}).get("status", "waiting"),
            "first": (a or {}).get("first", False), "original": n.get("original", True), "cents": (a or {}).get("cents", 0)}


def notes_state(request, u, vid, did, wt=None, via=""):
    """Everything the video page needs: what this person has done here, a note to judge once they've really
    watched, and (only after their own verdict or note) what everyone else said."""
    v = cat.get(vid)
    pl = pilot()
    who, uid, did_h, net_h = acting(request, u, did)
    ns, js, out = video_notes(vid)
    verdict = nstore.verdicts.get(nts.doc_id(vid, who)) if who else None
    mine_note = next((n for n in ns if who and n.get("who") == who), None)
    st = watch_state(wt, vid)
    watched = bool(st and v and nts.real(st, need_dur(v)))
    can_rate = bool(st and v and nts.judged(st, need_dur(v)))   # a few minutes in is enough to rate a review
    judge = None
    partner = hacker_status(u) == "approved"
    if uid and can_rate and not partner:  # partners never rate reviews: only viewers who aren't partners do
        judged_ids = {j["note_id"] for j in js if j.get("judge") == uid}
        sent_by = sent_by_partner(request, via)
        cands = [n for n in ns if n.get("mod") == "ok" and n.get("who") != who and n["id"] not in judged_ids
                 and out.get(n["id"], {}).get("status") == "waiting" and n.get("handle", "") not in sent_by
                 and not (did_h and n.get("did_h") == did_h) and n.get("net_h") != net_h]
        cands.sort(key=lambda n: (float(n["ts"]), n["id"]))  # the earliest note gets read first
        if cands:
            n = cands[0]
            judge = {"id": n["id"], "text": n["text"], "stance": n.get("stance"), "needs": n.get("needs", ""),
                     "moment_s": nts.fmt_moment(n.get("moment"))}
    html = ""
    if verdict or mine_note:
        retry_pending(ns, v["title"] if v else "")
        shown = sorted((n for n in ns if n.get("mod") == "ok" or n.get("who") == who), key=lambda n: (float(n["ts"]), n["id"]))
        tally = collections.Counter(x.get("verdict") for x in nstore.verdicts.by("vid", vid))
        html = templates.env.get_template("_notes.html").render(
            notes=[note_view(n, out.get(n["id"]), who) for n in shown], rec=tally.get(nts.REC, 0),
            skip=tally.get(nts.SKIP, 0), panel=nts.PANEL, qualify=nts.QUALIFY, pilot=pl, v=v)
    share = f"{BASE_URL}{v['url']}?via={ids.handle(uid)}" if (v and uid and mine_note and mine_note.get("mod") == "ok") else ""
    feat = featured(u, vid)
    held = bool(feat and feat["mine"] and feat["mine"]["state"] == "held")
    spot = {"featured": bool(feat), "held": held, "until": feat["mine"]["until"] if held else "",
            "open_left": feat["open_left"] if feat else 0, "paid_left": feat["paid_left"] if feat else 0} if feat else None
    return {"ok": True, "verdict": (verdict or {}).get("verdict"), "note": bool(mine_note), "signed_in": bool(uid),
            "hacker": partner, "paid_here": partner and held, "spot": spot,
            "earn": bool(pl) and hacker_status(u) == "none", "no_rate": partner and can_rate,
            "watched": watched, "judge": judge, "html": html, "share": share}


def read_json(raw):
    try:
        d = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        return None
    return d if isinstance(d, dict) else None


def clean_via(x):
    return x if isinstance(x, str) and nts.HANDLE_RE.match(x) else ""


def ref_from(request):
    """The partner handle from the last partner link this browser followed, if it was within 30 days."""
    h, _, t = request.cookies.get(REF_COOKIE, "").partition(".")
    try:
        fresh = nts.clock() - float(t) <= nts.REF_DAYS * 86400
    except ValueError:
        return ""
    return h if fresh and nts.HANDLE_RE.match(h) else ""


def sent_by_partner(request, via=""):
    """Handles of partners whose links brought this person here: they can't rate those partners' reviews."""
    return {h for h in (ref_from(request), via) if h}


def partner_by_handle(handle):
    """An approved partner's uid from their public handle. Admins never earn."""
    for uid, doc in store.find("handle", handle):
        if hacker_status(doc) == "approved" and uid not in ADMIN_UIDS:
            return uid
    return None


@app.post("/api/watch/beat")
async def api_watch_beat(request: Request):
    """Player heartbeat: returns a new signed watch token. The server, not the browser, decides what counts."""
    d = read_json(await request.body())
    v = cat.get(str((d or {}).get("vid") or ""))
    if not notes_ok() or d is None or not v:
        return JSONResponse({"ok": False}, 404)
    try:
        pos = min(max(float(d.get("pos") or 0), 0.0), 86400.0)
        played = min(max(float(d.get("played") or 0), 0.0), 600.0)
    except (TypeError, ValueError):
        pos, played = 0.0, 0.0
    try:
        tok, st = watcher.beat(d["wt"], pos, played) if d.get("wt") else watcher.start(v["vid"], d.get("rc"))
        if st.get("v") != v["vid"]:
            raise BadSignature("token for another video")
    except BadSignature:
        tok, st = watcher.start(v["vid"], None)
    return {"ok": True, "wt": tok, "real": nts.real(st, need_dur(v)), "judged": nts.judged(st, need_dur(v))}


@app.post("/api/notes/state")
async def api_notes_state(request: Request):
    d = read_json(await request.body())
    vid = str((d or {}).get("vid") or "")
    if not notes_ok() or d is None or not cat.get(vid):
        return JSONResponse({"ok": False}, 404)
    return notes_state(request, current_user(request), vid, d.get("did"), d.get("wt"), clean_via(d.get("via")))


def add_note(v, who, uid, did_h, net_h, text, stance, *, watched, via, paid=False, review=False, needs="", moment=None, extra=None,
             checked=None):
    """Store a note: checked for abuse (and, when paid, for being generic), compared with earlier notes on the
    same video and with the writer's own reviews of other videos (a template earns nothing), timestamped."""
    earlier = [n for n in nstore.notes.by("vid", v["vid"]) if nts.counts(n) and n.get("text")]
    mine = sorted((n for n in (nstore.notes.by("uid", uid) if uid else []) if n.get("vid") != v["vid"] and n.get("text")),
                  key=lambda n: -float(n.get("ts", 0)))[:20]
    sim = own = 0.0
    if earlier or mine:
        vecs = cat.embed([text] + [n["text"] for n in earlier] + [n["text"] for n in mine])
        first = vecs[0].tolist()
        sim = nts.originality(first, [x.tolist() for x in vecs[1:1 + len(earlier)]])
        own = nts.originality(first, [x.tolist() for x in vecs[1 + len(earlier):]])
    mod, why = checked or nts.moderate(f"{text} {needs}".strip(), "paid review" if paid else "note", v["title"])
    doc = {"vid": v["vid"], "who": who, "uid": uid, "handle": ids.handle(uid) if uid else "", "text": text,
           "stance": stance, "ts": nts.clock(), "mod": mod, "mod_why": why, "watched": watched,
           "original": sim < nts.ORIGINAL_MAX_SIM and own < nts.OWN_MAX_SIM, "sim": sim, "own_sim": own,
           "did_h": did_h, "net_h": net_h, "via": via,
           "paid": paid, "review": review, "needs": needs, "moment": moment, **(extra or {})}
    return nstore.notes.add(nts.doc_id(v["vid"], who), doc), mod


@app.post("/api/verdict")
async def api_verdict(request: Request):
    """Recommend or Say skip, after a watch the server verified. Unpaid, always. Recommend needs a note,
    except from a Hacker, who writes paid reviews separately."""
    d = read_json(await request.body())
    if not notes_ok() or d is None:
        return JSONResponse({"ok": False}, 404)
    try:
        st = watcher.load(str(d.get("wt") or ""))
    except BadSignature:
        return JSONResponse({"ok": False, "error": "Play the video here first."}, 400)
    v = cat.get(st.get("v", ""))
    verdict = d.get("verdict")
    if not v or verdict not in nts.VERDICTS:
        return JSONResponse({"ok": False, "error": "Something went wrong. Reload and try again."}, 400)
    if not nts.fresh(st):
        return JSONResponse({"ok": False, "error": "That was a while ago. Play it again to give a verdict."}, 400)
    if verdict == nts.REC and not nts.real(st, need_dur(v)):
        return JSONResponse({"ok": False, "error": "Recommend unlocks once you've watched most of it."}, 400)
    if verdict == nts.SKIP and not nts.judged(st, need_dur(v)):
        return JSONResponse({"ok": False, "error": "Watch a little more before calling it."}, 400)
    u = current_user(request)
    hacker = hacker_status(u) == "approved"
    text = nts.clean_text(d.get("text"))
    if (verdict == nts.REC and not hacker) or text:
        err = nts.check_note(text)
        if err:
            return JSONResponse({"ok": False, "error": err}, 400)
    who, uid, did_h, net_h = acting(request, u, d.get("did"))
    if not who:
        return JSONResponse({"ok": False, "error": "Your browser blocked storage. Sign in to give a verdict."}, 400)
    mine = nstore.verdicts.by("uid", uid) if uid else nstore.verdicts.by("who", who)
    if over_limit("verdict", mine, net_h):
        return JSONResponse({"ok": False, "error": "That's a lot of verdicts for one day. Try again tomorrow."}, 429)
    via = clean_via(d.get("via"))
    if not nstore.verdicts.add(nts.doc_id(v["vid"], who), {
            "vid": v["vid"], "who": who, "uid": uid, "verdict": verdict, "ts": nts.clock(), "did_h": did_h,
            "net_h": net_h, "secs": st.get("s", 0), "via": via}):
        return JSONResponse({"ok": False, "error": "You've already given your verdict on this video."}, 409)
    mod = ""
    if text:  # a note made with a verdict is never paid: it came from a watch
        _, mod = await asyncio.to_thread(add_note, v, who, uid, did_h, net_h, text, verdict, watched=True, via=via)
    log.info("funnel verdict who=%s vid=%s verdict=%s note=%d mod=%s via=%s", uid or "anon", v["vid"], verdict, len(text), mod, via)
    track_server("verdict_given", uid or who, {"verdict": verdict, "note": bool(text), "mod": mod, "signed_in": bool(uid),
                                               "via": bool(via)})
    return dict(notes_state(request, u, v["vid"], d.get("did"), d.get("wt"), via), mod=mod)


@app.post("/api/review")
async def api_review(request: Request):
    """A Hacker's review. No watch is checked or needed here: the review is judged by readers who watched."""
    d = read_json(await request.body())
    v = cat.get(str((d or {}).get("vid") or ""))
    if not notes_ok() or d is None or not v:
        return JSONResponse({"ok": False}, 404)
    u = current_user(request)
    if hacker_status(u) != "approved":
        return JSONResponse({"ok": False, "error": "Monetised reviews are for partners. Apply at /earn."}, 403)
    if d.get("seen") is not True:
        return JSONResponse({"ok": False, "error": "Only review videos you have watched."}, 400)
    stance = d.get("stance")
    if stance not in nts.VERDICTS:
        return JSONResponse({"ok": False, "error": "Say whether it's worth watching or one to skip."}, 400)
    text = nts.clean_text(d.get("text"))
    needs = nts.clean_text(d.get("needs"), nts.NEEDS_MAX)
    try:
        moment = nts.parse_moment(d.get("moment"))
    except ValueError:
        return JSONResponse({"ok": False, "error": "Write the moment as minutes:seconds, like 12:40."}, 400)
    err = nts.check_review(text, needs, moment, v["dur"])
    if err:
        return JSONResponse({"ok": False, "error": err}, 400)
    who, uid, did_h, net_h = acting(request, u, d.get("did"))
    if over_limit("note", nstore.notes.by("uid", uid), net_h):
        return JSONResponse({"ok": False, "error": "That's a lot of reviews for one day. Try again tomorrow."}, 429)
    # paid only in a spot this partner claimed on one of this week's 10; otherwise an unpaid review, which on a
    # featured video takes one of its 2 open spots
    w, paid, extra = this_week(), False, {}
    cid, c = live_claim(uid, v["vid"]) if w else ("", None)
    if c:  # a spot held from last week's 10 still pays for its full 48 hours
        paid, extra = True, {"week": c["week"], "slot": "paid"}
    elif w and v["vid"] in w["vids"]:
        if spots(w)[v["vid"]]["open_left"] > 0:
            extra = {"week": w["week"], "slot": "open"}
        else:
            return JSONResponse({"ok": False, "error": "All review spots on this video are taken this week. "
                                                      "Review another of this week's 10."}, 409)
    checked = None
    if paid:  # a paid review the automatic check holds back isn't kept, so the spot isn't wasted: fix it and post again
        checked = await asyncio.to_thread(nts.moderate, f"{text} {needs}".strip(), "paid review", v["title"])
        if checked[0] == "hidden":
            track_server("review_held_back", uid, {"paid": True, "why": checked[1]})
            return JSONResponse({"ok": False, "error": f"That can't go in a paid spot ({checked[1]}). Say who this video is for "
                                                      "and why, in your own words. Your spot is still yours."}, 400)
    added, mod = await asyncio.to_thread(add_note, v, who, uid, did_h, net_h, text, stance, watched=False,
                                         via=clean_via(d.get("via")), paid=paid, review=True, needs=needs, moment=moment,
                                         extra=extra, checked=checked)
    if not added:
        return JSONResponse({"ok": False, "error": "You've already reviewed this video."}, 409)
    if paid:
        nstore.claims.update(cid, {"status": "used", "note_id": nts.doc_id(v["vid"], who)})
    _week["spots"] = None
    log.info("funnel review uid=%s vid=%s stance=%s chars=%d paid=%s mod=%s", uid, v["vid"], stance, len(text), paid, mod)
    track_server("review_created", uid, {"paid": paid, "partner": True, "stance": stance, "mod": mod, "chars": len(text),
                                         "needs": bool(needs), "moment": moment is not None})
    return dict(notes_state(request, u, v["vid"], d.get("did"), d.get("wt")), mod=mod)


@app.post("/api/judge")
async def api_judge(request: Request):
    """Agree, Disagree or Can't judge on someone else's note. Signed in, and only after really watching the
    video here: the readers who check paid reviews are people who saw it, and they are never paid."""
    d = read_json(await request.body())
    if not notes_ok() or d is None:
        return JSONResponse({"ok": False}, 404)
    u = current_user(request)
    if not u:
        return JSONResponse({"ok": False, "error": "Sign in to rate reviews."}, 401)
    if hacker_status(u) == "approved":
        return JSONResponse({"ok": False, "error": "Partners don't rate reviews: only viewers who aren't partners do."}, 403)
    n = nstore.notes.get(str(d.get("note_id") or ""))
    vote = d.get("vote")
    if not n or n.get("mod") != "ok" or vote not in nts.VOTES:
        return JSONResponse({"ok": False, "error": "That review isn't available."}, 400)
    v = cat.get(n["vid"])
    st = watch_state(d.get("wt"), n["vid"])
    if not v or not st or not nts.judged(st, need_dur(v)):
        return JSONResponse({"ok": False, "error": "Watch a few minutes of it here first, then rate the review."}, 400)
    who, uid, did_h, net_h = acting(request, u, d.get("did"))
    if n.get("who") == who or n.get("uid") == uid:
        return JSONResponse({"ok": False, "error": "That's your own review."}, 400)
    if n.get("handle") and n["handle"] in sent_by_partner(request, clean_via(d.get("via"))):
        return JSONResponse({"ok": False, "error": "You came here through this partner's link, so you can't rate their review."}, 400)
    if over_limit("judgment", nstore.judgments.by("judge", uid), net_h):
        return JSONResponse({"ok": False, "error": "That's a lot of ratings for one day. Try again tomorrow."}, 429)
    t = nts.clock()
    if not nstore.judgments.add(f"{n['id']}_{uid}", {"note_id": n["id"], "vid": n["vid"], "judge": uid, "judge_who": who,
                                                     "vote": vote, "ts": t, "did_h": did_h, "net_h": net_h}):
        return JSONResponse({"ok": False, "error": "You've already rated this review."}, 409)
    _, _, out = video_notes(n["vid"])
    a = out.get(n["id"], {})
    log.info("funnel judgment uid=%s note=%s vote=%s", uid, n["id"], vote)
    track_server("review_rated", uid, {"vote": vote, "paid": bool(n.get("paid"))})
    if a.get("rated_ts") == t and a["status"] == "qualified" and n["vid"] in ADDED:
        vouch(n["vid"])
    if a.get("rated_ts") == t:  # this answer completed the panel
        track_server("review_counted" if a["status"] == "qualified" else "review_not_counted", n.get("uid") or n["who"],
                     {"hours": round((t - float(n["ts"])) / 3600, 1), "first": a.get("first", False), "paid": bool(n.get("paid"))})
    return dict(notes_state(request, u, n["vid"], d.get("did"), d.get("wt"), clean_via(d.get("via"))),
                tally={"vote": vote, "agree": a.get("agree", 0), "disagree": a.get("disagree", 0), "judged": a.get("judged", 0),
                       "status": a.get("status", "waiting"), "panel": nts.PANEL})


@app.post("/api/notes/merge")
async def api_notes_merge(request: Request):
    """On sign-in: move this browser's verdicts and notes onto the account, keeping their original times."""
    u = current_user(request)
    d = read_json(await request.body())
    if not u or d is None:
        return JSONResponse({"ok": False}, 401)
    anon = acting(request, None, d.get("did"))[0]
    if not anon:
        return {"ok": True, "moved": 0}
    moved = 0
    for coll, extra in ((nstore.verdicts, {}), (nstore.notes, {"handle": ids.handle(u["uid"])})):
        for doc in coll.by("who", anon):
            new = {k: val for k, val in doc.items() if k != "id"}
            new.update(extra, uid=u["uid"], who=u["uid"])
            if coll.add(nts.doc_id(doc["vid"], u["uid"]), new):
                moved += 1
            coll.delete(doc["id"])
    log.info("funnel notes-merged uid=%s moved=%d", u["uid"], moved)
    return {"ok": True, "moved": moved}


def all_assessed():
    """Every note's assessment, cached for a minute and dropped whenever a note or rating changes here."""
    if _assessed["v"] is None or nts.clock() - _assessed["t"] > 60:
        _assessed.update(v=nts.assess(nstore.notes.all(), nstore.judgments.all()), t=nts.clock())
    return _assessed["v"]


def person_record(uid):
    mine = nstore.notes.by("uid", uid)
    ns, js = [], []
    for vid in {n["vid"] for n in mine}:
        ns += nstore.notes.by("vid", vid)
        js += nstore.judgments.by("vid", vid)
    out = nts.assess(ns, js)
    rows = []
    for n in sorted(mine, key=lambda n: -float(n["ts"])):
        v = cat.get(n["vid"])
        if v:
            rows.append({"v": v, "n": note_view(n, out.get(n["id"]), n.get("who"))})
    return rows, nts.record([out[n["id"]] for n in mine if n["id"] in out])


def month_now():
    return nts.ym(nts.clock())


# ---------------- the weekly 10 and paid spots ----------------
_week = {"key": None, "doc": None, "t": -1e12, "spots": None}
_pot = {"month": None, "t": -1e12, "cents": nts.POT_BASE}


def week_pool():
    """Pilot-topic videos from the catalogue, best first: the candidates for a weekly 10."""
    pl = pilot()
    if not pl:
        return []
    return [v for v in cat.ranked if pl["vids"] is None or v["vid"] in pl["vids"]]


def week_finds(recent):
    """Submitted pilot-topic videos that viewers vouched for, oldest first, each with the partner who found it.
    The finder is left blank when they aren't a partner: the video can still be featured, with no finder fee."""
    pl, out = pilot(), []
    for d in sorted(nstore.added.all(), key=lambda d: float(d.get("ts", 0))):
        vid = d.get("vid", "")
        if d.get("status") != "vouched" or d.get("own") or vid in recent or not in_pilot(pl, vid):
            continue
        v = cat.get(vid)
        if v:
            finder = d.get("uid") or ""
            ok = finder and finder not in ADMIN_UIDS and hacker_status(store.get(finder)) == "approved"
            out.append((v, finder if ok else ""))
    return out


def make_week(key):
    """Pick and store the weekly 10 for week `key`. Its size follows how many of last week's paid reviews got
    rated in time, so spending only grows when real viewers do."""
    prev = nstore.weeks.get(nts.week_shift(key, -1))
    recent = set()
    for k in range(1, nts.WEEK_RECENT + 1):
        recent |= set((nstore.weeks.get(nts.week_shift(key, -k)) or {}).get("vids") or [])
    paid = [a for a in all_assessed().values() if a["paid"] and prev and a.get("week") == prev["week"]]
    in_time = sum(1 for a in paid if a["rated_ts"] and a["rated_ts"] < nts.week_start(key))
    size = nts.next_size((prev or {}).get("size"), in_time, len(paid))
    pool = week_pool()
    cands = [v for v in pool if v["vid"] not in recent and nts.WEEK_DUR_MIN <= int(v.get("dur") or 0) <= nts.WEEK_DUR_MAX]
    cands = cands[: size * 8]
    ok = nts.topic_fit(cands, (pilot() or {}).get("name", "")) if not (pilot() or {}).get("every") else None
    if ok is not None and len(ok) < size:  # too few passed the check to fill the week: keep the unchecked picks
        log.warning("topic fit kept %d of %d for week %s; picking unchecked", len(ok), len(cands), key)
        ok = None
    vids, found = nts.pick_week(key, pool, week_finds(recent), recent, size, ok=ok)
    doc = {"week": key, "start": nts.week_start(key), "vids": vids, "found": found, "size": size,
           "topic": PILOT_TOPIC, "fit_checked": ok is not None, "made": nts.clock(), "last_paid": len(paid), "last_in_time": in_time}
    if nstore.weeks.add(key, doc):  # the first server to make the week wins; the others read it back
        log.info("funnel week-made week=%s size=%d finds=%d", key, len(vids), len(found))
        track_server("week_made", "hackertube-server", {"week": key, "size": len(vids), "finds": len(found)})
        return doc
    return nstore.weeks.get(key) or doc


def this_week():
    """This week's 10, made on first use each week; None when no paid pilot runs. Cached for a minute."""
    if not pilot_on():
        return None
    key = nts.week_key(nts.clock())
    if _week["key"] != key or nts.clock() - _week["t"] > 60:
        _week.update(key=key, doc=nstore.weeks.get(key) or make_week(key), t=nts.clock(), spots=None)
    return _week["doc"]


def week_videos(w):
    return [v for v in (cat.get(x) for x in (w or {}).get("vids", [])) if v]


def week_claims(key):
    return nstore.claims.by("week", key)


def spots(w):
    """For each of the week's videos: paid spots left, and unpaid review spots left. Cached for 30 seconds, and
    dropped whenever a spot is claimed, released or used."""
    hit = _week["spots"]
    if hit and hit[0] == w["week"] and nts.clock() - hit[1] < 30:
        return hit[2]
    now_t, claims = nts.clock(), week_claims(w["week"])
    opened = collections.Counter(a["vid"] for a in all_assessed().values()
                                 if a.get("week") == w["week"] and a.get("slot") == "open")
    out = {vid: {"paid_left": max(0, nts.PAID_SPOTS - sum(1 for c in claims if c["vid"] == vid and nts.holding(c, now_t))),
                 "open_left": max(0, nts.OPEN_SPOTS - opened[vid])} for vid in w["vids"]}
    _week["spots"] = (w["week"], nts.clock(), out)
    return out


def partner_level(uid):
    return nts.level_for([a for a in all_assessed().values() if a.get("uid") == uid], nts.clock())


def live_claim(uid, vid):
    """This partner's held spot on a video, from this week's 10 or last week's: a spot claimed late on Sunday
    keeps its full 48 hours after the new week starts. Returns (claim id, claim) or ("", None)."""
    now_t = nts.clock()
    key = nts.week_key(now_t)
    for k in (key, nts.week_shift(key, -1)):
        cid = f"{k}_{vid}_{uid}"
        c = nstore.claims.get(cid)
        if c and c.get("status") == "held" and now_t < float(c.get("until", 0)):
            return cid, c
    return "", None


def carried_claims(uid):
    """Spots this partner still holds from last week's 10."""
    now_t = nts.clock()
    prev = nts.week_shift(nts.week_key(now_t), -1)
    return [c for c in week_claims(prev) if c["uid"] == uid and c.get("status") == "held" and now_t < float(c.get("until", 0))]


def held_claims():
    """Claimed spots still waiting for their review, this week and last (a hold can run past Monday)."""
    key, now_t = nts.week_key(nts.clock()), nts.clock()
    return [c for c in week_claims(key) + week_claims(nts.week_shift(key, -1))
            if c.get("status") == "held" and now_t < float(c.get("until", 0))]


def month_committed():
    """What this month could cost if everything waiting counted. New claims stop at the monthly cap."""
    return nts.committed(month_rows(month_now()), all_assessed(), held_claims(), nts.clock())


def spots_left(w):
    """Paid spots anyone can still claim this week: what the videos have left, within the monthly budget.
    Cached with the spots, since busy pages show it."""
    if not w:
        return 0
    hit = _week.get("left")
    if hit and _week["spots"] and hit[0] == w["week"] and hit[1] == _week["spots"][1]:
        return hit[2]
    free = sum(x["paid_left"] for x in spots(w).values())
    left = max(0, min(free, (nts.MONTH_CAP_CENTS - month_committed()) // nts.REVIEW_CENTS))
    _week["left"] = (w["week"], _week["spots"][1], left)
    return left


def claim_spot(u, vid):
    """Hold one of a featured video's paid spots for this partner for 48 hours. Returns '' or a problem code."""
    w, now_t, uid = this_week(), nts.clock(), u["uid"]
    if uid in ADMIN_UIDS:
        return "staff"
    if not w or vid not in w["vids"]:
        return "notweek"
    cid = f"{w['week']}_{vid}_{uid}"
    mine = nstore.claims.get(cid)
    if mine and mine.get("status") != "lost":
        return "again"
    claims, assessed = week_claims(w["week"]), all_assessed()
    pending = {a["uid"] for a in assessed.values() if a["paid"] and a["status"] == "waiting"}
    code = nts.claim_problem(
        partner_level(uid), w["week"], now_t, featured=True, reviewed=bool(nstore.notes.get(nts.doc_id(vid, uid))),
        own=bool(u.get("yt_channel_id")) and u.get("yt_channel_id") == (cat.get(vid) or {}).get("channel_id"),
        mine_week=[c for c in claims if c["uid"] == uid], video_claims=[c for c in claims if c["vid"] == vid],
        committed=month_committed(), cost=nts.spot_cost(uid, assessed, pending))
    if code:
        return code
    doc = {"week": w["week"], "vid": vid, "uid": uid, "ts": now_t, "until": now_t + nts.HOLD_HOURS * 3600,
           "status": "held", "note_id": ""}
    if mine:
        nstore.claims.update(cid, doc)
    elif not nstore.claims.add(cid, doc):
        return "again"
    _week["spots"] = None
    # two partners taking the last spot at once: the earlier claim keeps it and the later one steps back
    holders = sorted((c for c in week_claims(w["week"]) if c["vid"] == vid and nts.holding(c, now_t)),
                     key=lambda c: (float(c["ts"]), c["uid"]))
    if uid not in [c["uid"] for c in holders[: nts.PAID_SPOTS]]:
        nstore.claims.update(cid, {"status": "lost"})
        return "full"
    if month_committed() > nts.MONTH_CAP_CENTS:
        nstore.claims.update(cid, {"status": "lost"})
        return "budget"
    log.info("funnel spot-claimed uid=%s vid=%s week=%s", uid, vid, w["week"])
    track_server("spot_claimed", uid, {"vid": vid, "week": w["week"], "level": partner_level(uid)["key"]})
    return ""


def release_spot(u, vid):
    """Give a held spot back for someone else. It still counts against this partner's spots for the week."""
    cid, c = live_claim(u["uid"], vid) if this_week() else ("", None)
    if not c:
        return False
    nstore.claims.update(cid, {"status": "released"})
    _week["spots"] = None
    track_server("spot_released", u["uid"], {"vid": vid, "week": c["week"]})
    return True


def featured(u, vid):
    """What a video page shows when the video is in this week's 10: spots left, and this partner's spot."""
    w = this_week()
    if not w:
        return None
    partner = hacker_status(u) == "approved"
    if vid not in w["vids"]:  # not in this week's 10, but this partner may still hold a spot on it from last week's
        c = live_claim(u["uid"], vid)[1] if partner else None
        if not c:
            return None
        return {"week": c["week"], "paid_left": 0, "open_left": 0, "partner": True, "carried": True,
                "mine": {"state": "held", "until": nts.when_utc(c["until"])}, "found_by": "", "found_handle": "",
                "review_cents": nts.REVIEW_CENTS}
    sp = spots(w)[vid]
    mine = None
    if partner:
        c = nstore.claims.get(f"{w['week']}_{vid}_{u['uid']}")
        if c and c.get("status") == "used":
            mine = {"state": "used"}
        elif c and nts.holding(c, nts.clock()):
            mine = {"state": "held", "until": nts.when_utc(c["until"])}
        elif c and c.get("status") in ("held", "released"):
            mine = {"state": "gone"}
    finder = (w.get("found") or {}).get(vid)
    return {"week": w["week"], "paid_left": sp["paid_left"], "open_left": sp["open_left"], "partner": partner,
            "mine": mine, "found_by": display_name(finder) if finder else "",
            "found_handle": ids.handle(finder) if finder else "", "review_cents": nts.REVIEW_CENTS}


def pot_amount(month):
    """A month's Diamond pot: the base, or last month's plus a step when nobody won it, up to the cap."""
    prev_m = nts.month_before(month)
    prev = nstore.pots.get(prev_m)
    if not prev or prev.get("status") != "rolled":
        return nts.POT_BASE
    return min(nts.POT_MAX, pot_amount(prev_m) + nts.POT_STEP)


def pot_now():
    """This month's pot, cached for a minute: it shows on busy pages."""
    m = month_now()
    if _pot["month"] != m or nts.clock() - _pot["t"] > 60:
        _pot.update(month=m, t=nts.clock(), cents=pot_amount(m))
    return _pot["cents"]


def open_pot(month, rows):
    """When a month closes, line up who could win its pot. A person checks the first in line before paying;
    with nobody in line, the pot rolls over to next month."""
    winners = {p.get("winner") for p in nstore.pots.all() if p.get("status") == "won"}
    line = [r["uid"] for r in nts.pot_line(rows, winners, ADMIN_UIDS)]
    nstore.pots.add(month, {"month": month, "status": "review" if line else "rolled", "line": line,
                            "cents": pot_amount(month), "opened": now()})
    _pot["month"] = None


def typical_cents(month):
    """What the typical partner earned in a finished month: the middle of everyone who had a paid review rated
    or a finder fee, counting those who earned nothing. Shown next to every big number."""
    marker = nstore.statements.get(f"{month}__closed") or {}
    totals = [int(x["total_cents"]) for x in nstore.statements.by("month", month)
              if not x.get("marker") and x.get("kind") != "pot"]
    n = max(int(marker.get("partners") or 0), len(totals))
    return nts.median(totals + [0] * (n - len(totals))) if n else None


def pot_race():
    """The month's Diamond pot and the race for it, for everyone to see: the top five so far, who has won
    before, and what the typical partner earned last month."""
    month = month_now()
    rows = month_rows(month)
    pots = nstore.pots.all()
    winners = {p.get("winner") for p in pots if p.get("status") == "won"}
    line = {r["uid"] for r in nts.pot_line(rows, winners, ADMIN_UIDS)}
    board = sorted((r for r in rows.values() if r["counted"] > 0 and r["uid"] not in ADMIN_UIDS),
                   key=lambda r: (-r["counted"], -nts.agreement(r), r["uid"]))[:5]
    hall = [{"month": month_name(p["month"]), "name": display_name(p["winner"]), "handle": ids.handle(p["winner"]),
             "cents": int(p.get("cents") or 0)} for p in sorted(pots, key=lambda p: p["month"], reverse=True)
            if p.get("status") == "won" and p.get("winner")]
    last = nts.month_before(month)
    return {"cents": pot_now(), "month_name": month_name(month), "min": nts.POT_MIN, "hall": hall,
            "board": [{"name": display_name(r["uid"]), "handle": ids.handle(r["uid"]), "counted": r["counted"],
                       "agreement": int(round(100 * nts.agreement(r))), "in_line": r["uid"] in line,
                       "won_before": r["uid"] in winners} for r in board],
            "typical": typical_cents(last), "typical_month": month_name(last)}


_closed = {"month": None}   # the month this instance last checked that every earlier month is frozen


_weeks_all = {"t": -1e12, "v": []}


def all_weeks():
    """Every weekly 10 so far (finder fees come from them), cached for a minute."""
    if nts.clock() - _weeks_all["t"] > 60 or (_week["doc"] and _week["doc"]["week"] not in {w["week"] for w in _weeks_all["v"]}):
        _weeks_all.update(t=nts.clock(), v=nstore.weeks.all())
    return _weeks_all["v"]


def finalise_months():
    """Freeze every finished month that had any activity, once, and line up its Diamond pot. A frozen month
    never changes afterwards, so a later rating, a deleted review or an old review counting can't move what was
    already earned."""
    now_m = month_now()
    if _closed["month"] == now_m:
        return
    assessed, weeks = all_assessed(), nstore.weeks.all()
    months = {nts.ym(a["rated_ts"]) for a in assessed.values() if a["paid"] and a["uid"] and a["rated_ts"]}
    months |= {nts.ym(nts.week_start(w["week"])) for w in weeks}
    for m in sorted(x for x in months if x < now_m):
        if nstore.statements.get(f"{m}__closed"):
            continue
        rows = nts.earnings(assessed, m, weeks, exclude=ADMIN_UIDS)
        total = sum(r["total_cents"] for r in rows.values())
        for uid, r in rows.items():
            if r["total_cents"] > 0:
                nstore.statements.add(f"{m}_{uid}", dict(r, month=m, kind="month", finalised=now()))
        if not nstore.pots.get(m):
            open_pot(m, rows)
        nstore.statements.add(f"{m}__closed", {"month": m, "marker": True, "partners": len(rows), "total_cents": total,
                                                "finalised": now()})
        log.info("funnel month-finalised month=%s partners=%d total=%d", m, len(rows), total)
        track_server("month_finalised", "hackertube-server", {"month": m, "partners": len(rows), "total_cents": total})
    _closed["month"] = now_m


def month_rows(month):
    """Each partner's earnings for a month: live while it's open, frozen once it has ended. A Diamond pot is
    its own statement line, outside these rows."""
    if month < month_now():
        finalise_months()
        return {x["uid"]: x for x in nstore.statements.by("month", month)
                if not x.get("marker") and x.get("kind") != "pot"}
    return nts.earnings(all_assessed(), month, all_weeks(), exclude=ADMIN_UIDS)


def partner_money(uid, live_rows=None):
    """A partner's money: finished months and any Diamond pot, what's earned so far this month, what has been
    paid, and when the next payment is due. The first payment, from US$10, goes out within a week and can
    include this month's earnings; after that, payments from US$100 come out of finished months by the 7th."""
    finalise_months()
    sts = sorted((x for x in nstore.statements.by("uid", uid) if not x.get("marker")), key=lambda x: x["month"], reverse=True)
    pays = sorted(nstore.payouts.by("uid", uid), key=lambda x: -float(x["ts"]))
    settled = sum(int(x["total_cents"]) for x in sts) - sum(int(x["cents"]) for x in pays)
    live = int(((month_rows(month_now()) if live_rows is None else live_rows).get(uid) or {}).get("total_cents", 0))
    first = not pays
    payable = max(0, settled + live if first else settled)
    thr = nts.threshold(pays)
    return {"statements": sts, "payouts": pays, "balance": settled + live, "settled": settled, "live": live,
            "payable": payable, "threshold": thr, "first": first,
            "pct": max(0, min(100, int(100 * payable / thr))), "due": payable >= thr}


def day(ts):
    return datetime.datetime.fromtimestamp(float(ts), datetime.timezone.utc).strftime("%-d %b %Y")


def month_name(month):
    y, m = (int(x) for x in month.split("-"))
    return datetime.date(y, m, 1).strftime("%B %Y")


def partner_view(u):
    """Everything a partner's Earn page shows: this month so far, level and paid spots, this week's spots,
    balance, payments and paid reviews."""
    uid, month, now_t = u["uid"], month_now(), nts.clock()
    rows = month_rows(month)
    row = rows.get(uid) or nts.blank(uid)
    money = partner_money(uid, rows)
    lv = partner_level(uid)
    a_all = all_assessed()
    firsts = set(nts.first_ever(a_all).values())
    lines = []
    for n in sorted(nstore.notes.by("uid", uid), key=lambda n: -float(n["ts"])):
        a, v = a_all.get(n["id"]) or {}, cat.get(n["vid"])
        if not n.get("paid") or not v:
            continue
        lines.append({"v": v, "status": a.get("status", "waiting"), "judged": a.get("judged", 0), "agree": a.get("agree", 0),
                      "mod": n.get("mod"), "original": n.get("original", True), "first_ever": n["id"] in firsts,
                      "when": month_name(nts.ym(a["rated_ts"])) if a.get("rated_ts") else "",
                      "cents": (nts.FIRST_EVER_CENTS if n["id"] in firsts else a["cents"]) if a.get("cents") else 0})
        if len(lines) >= 25:
            break
    w = this_week()
    claims, used = [], 0
    for c in sorted(week_claims(w["week"]) if w else [], key=lambda c: float(c["ts"])):
        if c["uid"] != uid or c.get("status") == "lost":
            continue
        used += 1
        v = cat.get(c["vid"])
        if v:
            state = ("used" if c["status"] == "used" else "held" if nts.holding(c, now_t)
                     else "released" if c["status"] == "released" else "expired")
            claims.append({"v": v, "state": state, "until": nts.when_utc(c["until"])})
    for c in carried_claims(uid):  # held from last week's 10; they don't use this week's spots
        v = cat.get(c["vid"])
        if v:
            claims.append({"v": v, "state": "held", "until": nts.when_utc(c["until"]), "carried": True})
    today = datetime.datetime.fromtimestamp(now_t, datetime.timezone.utc).date()
    due_by = datetime.date(today.year, today.month, nts.PAY_DAY).strftime("%-d %B %Y") if today.day <= nts.PAY_DAY else ""
    return {"row": row, "level": lv, "money": money, "lines": lines, "month": month, "month_name": month_name(month),
            "pay_by": nts.pay_by(month), "due_by": due_by, "paypal": ((u.get("payout") or {}).get("paypal") or ""),
            "claims": claims, "spots_used": used, "spots_left": max(0, lv["spots"] - used),
            "opens": nts.when_utc(nts.opens_at(w["week"], lv)) if w else "",
            "open_now": bool(w) and now_t >= nts.opens_at(w["week"], lv),
            "share_link": f"{BASE_URL}/r/{ids.handle(uid)}"}


def week_page_ctx(u):
    """What the weekly 10 page shows: each video with its spots, this partner's allowance, the pot, the drop."""
    w = this_week()
    if not w:
        return None
    sp = spots(w)
    partner = hacker_status(u) == "approved"
    mine = {}
    if partner:
        for c in week_claims(w["week"]):
            if c["uid"] == u["uid"] and c.get("status") != "lost":
                mine[c["vid"]] = "used" if c["status"] == "used" else "held" if nts.holding(c, nts.clock()) else "gone"
    lv = partner_level(u["uid"]) if partner else nts.LEVELS[0]
    nxt = nts.week_shift(w["week"], 1)
    return {"week": w, "videos": week_videos(w), "spots": sp, "mine": mine, "partner": partner, "level": lv,
            "left": spots_left(w), "opens": nts.when_utc(nts.opens_at(w["week"], lv)),
            "open_now": nts.clock() >= nts.opens_at(w["week"], lv), "next_drop": nts.when_utc(nts.week_start(nxt)),
            "found": {vid: display_name(x) for vid, x in (w.get("found") or {}).items() if x},
            "carried": [dict(v=cat.get(c["vid"]), until=nts.when_utc(c["until"])) for c in carried_claims(u["uid"])
                        if cat.get(c["vid"])] if partner else []}


@app.get("/r/{handle}", response_class=HTMLResponse)
def recommender(request: Request, handle: str):
    if not notes_ok() or not nts.HANDLE_RE.match(handle):
        return page(request, "404.html", {"title": "Not found", "description": ""}, 404)
    mine = [n for n in nstore.notes.by("handle", handle) if n.get("uid")]
    if not mine:
        return page(request, "404.html", {"title": "Not found", "description": ""}, 404)
    uid = mine[0]["uid"]
    rows, rec = person_record(uid)
    rows = [r for r in rows if r["n"]["mod"] == "ok"]
    name = display_name(uid)
    return page(request, "recommender.html", {
        "title": f"{name}: reviews on HackerTube AI", "noindex": True, "is_hacker": hacker_status(store.get(uid)) == "approved",
        "description": f"Reviews by {name}, and how many viewers agreed.",
        "name": name, "rows": rows, "rec": rec, "panel": nts.PANEL, "qualify": nts.QUALIFY})


@app.get("/hackers")
def hackers_old(request: Request):
    if not NOTES_ON:
        return page(request, "404.html", {"title": "Not found", "description": ""}, 404)
    return RedirectResponse("/earn", 301)


def earn_numbers():
    """The program's numbers, for the pitch, the terms and the dashboard alike."""
    return {"review": nts.money(nts.REVIEW_CENTS), "first_review": nts.money(nts.FIRST_EVER_CENTS),
            "finder": nts.money(nts.FINDER_CENTS), "pot_base": nts.money(nts.POT_BASE), "pot_max": nts.money(nts.POT_MAX),
            "pot_step": nts.money(nts.POT_STEP), "pot_min": nts.POT_MIN, "first_cashout": nts.money(nts.FIRST_CASHOUT_CENTS),
            "cashout": nts.money(nts.CASHOUT_CENTS), "pay_day": nts.PAY_DAY, "first_pay_days": nts.FIRST_PAY_DAYS,
            "panel": nts.PANEL, "qualify": nts.QUALIFY, "levels": nts.LEVELS, "level_rate": int(nts.LEVEL_RATE * 100),
            "level_days": nts.LEVEL_DAYS, "paid_spots": nts.PAID_SPOTS, "open_spots": nts.OPEN_SPOTS,
            "hold_hours": nts.HOLD_HOURS, "judge_per_author": nts.JUDGE_PER_AUTHOR, "cap": nts.money(nts.MONTH_CAP_CENTS),
            "money": nts.money}


@app.get("/earn", response_class=HTMLResponse)
def earn_page(request: Request):
    """The Partner Program, in the words people know from YouTube monetisation. Partners see their earnings."""
    if not NOTES_ON:
        return page(request, "404.html", {"title": "Not found", "description": ""}, 404)
    u = current_user(request)
    pl = pilot()
    status = hacker_status(u)
    mine = partner_view(u) if (u and status == "approved" and pl) else None
    w = this_week()
    msg = request.query_params.get("msg", "")
    return page(request, "hackers.html", dict(earn_numbers(), **{
        "title": "Get monetised for your reviews: the HackerTube Partner Program",
        "description": "Get paid US$5 for every review viewers agree with, from your very first one. A US$1,000 Diamond pot every month.",
        "noindex": True, "status": status, "pilot": pl, "mine": mine, "week": w, "week_videos": week_videos(w)[:5],
        "spots_left": spots_left(w), "pot": pot_race() if pl else None,
        "next_drop": nts.when_utc(nts.week_start(nts.week_shift(w["week"], 1))) if w else "",
        "msg": msg, "claim_msg": nts.claim_message(msg, mine["level"] if mine else nts.LEVELS[0], w["week"])
        if (msg in nts.CLAIM_MSGS and w) else "", "day": day, "month_name": month_name}))


@app.get("/week", response_class=HTMLResponse)
def week_page(request: Request):
    """This week's 10: the videos with paid review spots, open to everyone to watch and rate."""
    u = current_user(request)
    ctx = week_page_ctx(u) if NOTES_ON else None
    if not ctx:
        return page(request, "404.html", {"title": "Not found", "description": ""}, 404)
    msg = request.query_params.get("msg", "")
    return page(request, "week.html", dict(earn_numbers(), **ctx, **{
        "title": "This week's 10: videos worth your time, reviewed by partners and judged by viewers",
        "description": f"{len(ctx['videos'])} videos picked this week. Watch one, then say whether you agree with "
                       "the reviews. Partners get paid for reviews viewers agree with.",
        "pot": pot_race(), "msg": nts.claim_message(msg, ctx["level"], ctx["week"]["week"]) if msg in nts.CLAIM_MSGS else "",
        "ok": msg in ("claimed", "released")}))


@app.post("/week/claim")
async def week_claim(request: Request, vid: str = Form(""), next: str = Form("/week")):
    """Claim a paid spot on one of this week's 10."""
    u = current_user(request)
    back = safe_next(next)
    if not NOTES_ON or not u:
        return RedirectResponse(f"/auth/login?next={urllib.parse.quote(back)}", 303)
    if hacker_status(u) != "approved":
        return RedirectResponse("/earn", 303)
    code = await asyncio.to_thread(claim_spot, u, vid) or "claimed"
    sep = "&" if "?" in back else "?"
    return RedirectResponse(f"{back}{sep}msg={code}#spot" if back.startswith("/v/") else f"{back}{sep}msg={code}", 303)


@app.post("/week/release")
async def week_release(request: Request, vid: str = Form(""), next: str = Form("/week")):
    u = current_user(request)
    back = safe_next(next)
    if not NOTES_ON or not u:
        return RedirectResponse("/week", 303)
    ok = await asyncio.to_thread(release_spot, u, vid)
    sep = "&" if "?" in back else "?"
    return RedirectResponse(f"{back}{sep}msg={'released' if ok else 'notweek'}", 303)


@app.post("/earn/paypal")
async def earn_paypal(request: Request, paypal: str = Form("")):
    """Where a partner wants to be paid. Only a PayPal email: the site never holds bank details. One PayPal
    account per partner, so one person can't collect for many accounts."""
    u = current_user(request)
    if not NOTES_ON or not u or hacker_status(u) != "approved":
        return RedirectResponse("/earn", 303)
    paypal = paypal.strip().lower()
    if paypal and not EMAIL_RE.match(paypal):
        return RedirectResponse("/earn?msg=paypal-invalid#payment", 303)
    if paypal and any(uid != u["uid"] for uid, _ in store.find("payout.paypal", paypal)):
        log.info("funnel paypal-shared uid=%s", u["uid"])
        track_server("payout_method_shared", u["uid"], {})
        return RedirectResponse("/earn?msg=paypal-taken#payment", 303)
    store.upsert(u["uid"], {"payout": {"paypal": paypal, "set": now()}})
    track_server("payout_method_saved", u["uid"], {"has": bool(paypal)})
    return RedirectResponse("/earn?msg=paypal-saved#payment", 303)


@app.post("/hackers/apply")
async def hackers_apply(request: Request, why: str = Form("")):
    if not NOTES_ON:
        return RedirectResponse("/", 303)
    u = current_user(request)
    if not u:
        return RedirectResponse("/auth/login?next=/earn", 303)
    if hacker_status(u) in ("applied", "approved"):
        return RedirectResponse("/earn", 303)
    status = "approved" if HACKER_AUTO_APPROVE else "applied"
    store.upsert(u["uid"], {"handle": ids.handle(u["uid"]),
                            "hacker": {"status": status, "applied": now(), "why": nts.clean_text(why, 300),
                                       **({"decided": now()} if status == "approved" else {})}})
    log.info("funnel hacker-applied uid=%s status=%s", u["uid"], status)
    track_server("partner_applied", u["uid"], {"status": status, "why": bool(why.strip())})
    return RedirectResponse(f"/earn?msg={status}", 303)


@app.get("/hackers/admin", response_class=HTMLResponse)
def hackers_admin(request: Request):
    u = current_user(request)
    if not NOTES_ON or not is_admin(u):
        return page(request, "404.html", {"title": "Not found", "description": ""}, 404)
    people = []
    for status in ("applied", "approved", "rejected"):
        for uid, doc in store.find("hacker.status", status):
            _, rec = person_record(uid)
            people.append({"uid": uid, "name": doc.get("rec_name") or f"Reviewer {ids.handle(uid)}",
                           "handle": ids.handle(uid), "status": status, "why": (doc.get("hacker") or {}).get("why", ""),
                           "applied": (doc.get("hacker") or {}).get("applied", "")[:10], "rec": rec})
    month = request.query_params.get("month") or month_now()
    month = month if re.fullmatch(r"\d{4}-\d{2}", month) else month_now()
    w = this_week()
    return page(request, "hackers_admin.html", {
        "title": "Partner Program: admin", "description": "", "noindex": True, "people": people, "pilot": pilot(),
        "rows": statement_rows(month), "month": month, "frozen": month < month_now(), "balances": balance_rows(),
        "pots": pots_view(), "pot_now": pot_now(), "week": w, "week_videos": week_videos(w), "spots": spots(w) if w else {},
        "committed": month_committed() if w else 0, "cap": nts.MONTH_CAP_CENTS, "money": nts.money,
        "msg": request.query_params.get("msg", ""), "day": day, "first_pay_days": nts.FIRST_PAY_DAYS})


def statement_rows(month):
    pots = {x["uid"]: int(x["total_cents"]) for x in nstore.statements.by("month", month) if x.get("kind") == "pot"}
    rows = month_rows(month)
    out = []
    for uid in set(rows) | set(pots):
        r = rows.get(uid) or nts.blank(uid)
        out.append(dict(r, name=display_name(uid), handle=ids.handle(uid), pot_cents=pots.get(uid, 0),
                        all_cents=r["total_cents"] + pots.get(uid, 0)))
    return sorted(out, key=lambda r: -r["all_cents"])


def partner_flags():
    """For every partner with paid reviews that counted: plain warnings for a person to check before paying."""
    marks = collections.defaultdict(set)   # a browser or network -> the partners who wrote reviews from it
    for n in nstore.notes.all():
        if n.get("uid") and n.get("review"):
            for k in (n.get("did_h"), n.get("net_h")):
                if k:
                    marks[k].add(n["uid"])
    made = {}

    def created(uid):
        if uid not in made:
            c = (store.get(uid) or {}).get("created") or ""
            try:
                made[uid] = datetime.datetime.fromisoformat(c).timestamp() if c else None
            except ValueError:
                made[uid] = None
        return made[uid]

    by_uid = collections.defaultdict(list)
    for a in all_assessed().values():
        if a["cents"] and a["uid"]:
            by_uid[a["uid"]].append(a)
    out = {}
    for uid, mine in by_uid.items():
        judges = {j["judge"] for a in mine for j in a["panel"] if j.get("judge")}
        when = {j: created(j) for j in judges if created(j) is not None}
        out[uid] = nts.risk_flags(mine, when, any(len(s) > 1 for s in marks.values() if uid in s))
    return out


def balance_rows():
    """Every partner with money owed or paid: what's payable now, the threshold, PayPal email and warnings."""
    out, live, flags = [], month_rows(month_now()), partner_flags()
    for uid, doc in store.find("hacker.status", "approved"):
        m = partner_money(uid, live)
        if m["statements"] or m["payouts"] or m["live"]:
            out.append({"uid": uid, "name": doc.get("rec_name") or f"Reviewer {ids.handle(uid)}", "handle": ids.handle(uid),
                        "paypal": ((doc.get("payout") or {}).get("paypal") or ""), "flags": flags.get(uid, []), **m})
    return sorted(out, key=lambda x: (not x["due"], -x["payable"]))


def pots_view():
    """Every month's Diamond pot: who is in line (with warnings), who won, or that it rolled over."""
    flags, out = partner_flags(), []
    for p in sorted(nstore.pots.all(), key=lambda p: p["month"], reverse=True):
        rows = month_rows(p["month"])
        line = [{"uid": uid, "name": display_name(uid), "handle": ids.handle(uid), "counted": (rows.get(uid) or {}).get("counted", 0),
                 "rated": (rows.get(uid) or {}).get("rated", 0), "agreement": int(round(100 * nts.agreement(rows.get(uid) or {}))),
                 "flags": flags.get(uid, [])} for uid in (p.get("line") or [])]
        out.append(dict(p, month_name=month_name(p["month"]), line_rows=line,
                        amount=pot_amount(p["month"]) if p.get("status") == "review" else int(p.get("cents") or 0),
                        winner_name=display_name(p["winner"]) if p.get("winner") else ""))
    return out


@app.post("/hackers/admin/pot")
async def hackers_pot(request: Request, month: str = Form(""), uid: str = Form(""), decision: str = Form("")):
    """Crown a month's Diamond after checking their reviews and raters by hand, or pass them over: the next in line
    is then up, and with nobody left the pot rolls over to next month."""
    u = current_user(request)
    p = nstore.pots.get(month) if re.fullmatch(r"\d{4}-\d{2}", month or "") else None
    if (not NOTES_ON or not is_admin(u) or not p or p.get("status") != "review" or uid not in (p.get("line") or [])
            or decision not in ("crown", "pass")):
        return RedirectResponse("/hackers/admin#pots", 303)
    if decision == "crown":
        cents = pot_amount(month)
        nstore.pots.update(month, {"status": "won", "winner": uid, "cents": cents, "decided": now(), "by": u["uid"]})
        nstore.statements.add(f"{month}_pot_{uid}", {"uid": uid, "month": month, "kind": "pot", "total_cents": cents,
                                                      "counted": (month_rows(month).get(uid) or {}).get("counted", 0),
                                                      "finalised": now()})
        log.info("funnel diamond-crowned month=%s uid=%s cents=%d", month, uid, cents)
        track_server("diamond_crowned", uid, {"month": month, "cents": cents})
    else:
        line = [x for x in p["line"] if x != uid]
        upd = {"line": line, "passed": (p.get("passed") or []) + [uid]}
        if not line:
            upd.update(status="rolled", cents=pot_amount(month), decided=now(), by=u["uid"])
        nstore.pots.update(month, upd)
        log.info("funnel diamond-passed month=%s uid=%s", month, uid)
        track_server("diamond_passed", uid, {"month": month})
    _pot["month"] = None
    return RedirectResponse("/hackers/admin?msg=pot#pots", 303)


@app.post("/hackers/admin/decide")
async def hackers_decide(request: Request, uid: str = Form(""), decision: str = Form("")):
    u = current_user(request)
    if not is_admin(u) or decision not in ("approved", "rejected") or not store.get(uid):
        return RedirectResponse("/", 303)
    h = dict((store.get(uid) or {}).get("hacker") or {})
    h.update(status=decision, decided=now())
    store.upsert(uid, {"hacker": h, "handle": ids.handle(uid)})
    log.info("funnel hacker-decided uid=%s by=%s decision=%s", uid, u["uid"], decision)
    track_server("partner_decided", uid, {"decision": decision})
    return RedirectResponse("/hackers/admin", 303)


@app.post("/hackers/admin/paid")
async def hackers_paid(request: Request, uid: str = Form(""), cents: int = Form(0), ref: str = Form("")):
    """Record a payment made by hand (PayPal or bank). The site never moves money."""
    u = current_user(request)
    if not NOTES_ON or not is_admin(u) or not store.get(uid) or cents <= 0:
        return RedirectResponse("/hackers/admin", 303)
    if cents > partner_money(uid)["payable"]:
        return RedirectResponse("/hackers/admin?msg=over#balances", 303)
    to = (((store.get(uid) or {}).get("payout") or {}).get("paypal") or "")
    t = nts.clock()
    nstore.payouts.add(f"{uid}_{int(t * 1000)}", {"uid": uid, "cents": int(cents), "ts": t, "method": "PayPal" if to else "Other",
                                                  "to": to, "ref": nts.clean_text(ref, 80), "by": u["uid"]})
    log.info("funnel payout-marked uid=%s cents=%d by=%s", uid, cents, u["uid"])
    track_server("payout_marked", uid, {"cents": int(cents), "method": "paypal" if to else "other"})
    return RedirectResponse("/hackers/admin?msg=paid#balances", 303)


def csv_cell(x):
    return '"' + str(x).replace('"', "'") + '"'


@app.get("/hackers/statement.csv")
def hackers_statement_csv(request: Request, month: str = ""):
    u = current_user(request)
    if not is_admin(u):
        return PlainTextResponse("", 404)
    month = month if re.fullmatch(r"\d{4}-\d{2}", month or "") else month_now()
    lines = ["month,uid,name,handle,reviews_counted,reviews_rated,first_review_bonus,finds,review_pay,finder_fees,diamond_pot,total,currency"]
    for r in statement_rows(month):
        lines.append(",".join([month, r["uid"], csv_cell(r["name"]), r["handle"], str(r["counted"]), str(r["rated"]),
                               str(r.get("first_ever", 0)), str(r.get("finds", 0)), f"{r['review_cents'] / 100:.2f}",
                               f"{r.get('finder_cents', 0) / 100:.2f}", f"{r['pot_cents'] / 100:.2f}",
                               f"{r['all_cents'] / 100:.2f}", nts.CURRENCY]))
    return PlainTextResponse("\n".join(lines) + "\n", headers={"Content-Disposition": f"attachment; filename=partners-{month}.csv"})


@app.get("/hackers/payouts.csv")
def hackers_payouts_csv(request: Request):
    """Who to pay now: balances at or over their threshold, with PayPal emails."""
    u = current_user(request)
    if not is_admin(u):
        return PlainTextResponse("", 404)
    lines = ["uid,name,handle,paypal,balance,payable,threshold,due,first_payment,warnings,currency"]
    for b in balance_rows():
        lines.append(",".join([b["uid"], csv_cell(b["name"]), b["handle"], csv_cell(b["paypal"]), f"{b['balance'] / 100:.2f}",
                               f"{b['payable'] / 100:.2f}", f"{b['threshold'] / 100:.2f}", "yes" if b["due"] else "no",
                               "yes" if b["first"] else "no", csv_cell("; ".join(b["flags"])), nts.CURRENCY]))
    return PlainTextResponse("\n".join(lines) + "\n", headers={"Content-Disposition": "attachment; filename=partner-payouts.csv"})


@app.post("/me/name")
async def me_name(request: Request, name: str = Form("")):
    u = current_user(request)
    if not u:
        return RedirectResponse("/", 303)
    name = nts.clean_text(name, nts.NAME_MAX + 1)
    if not name:
        store.upsert(u["uid"], {"rec_name": ""})
        _names.pop(u["uid"], None)
        return RedirectResponse("/me?msg=name-cleared#notes", 303)
    if nts.check_name(name):
        return RedirectResponse("/me?msg=name-invalid#notes", 303)
    mod, _ = await asyncio.to_thread(nts.moderate, name, "display name")
    if mod != "ok":
        return RedirectResponse("/me?msg=name-rejected#notes", 303)
    store.upsert(u["uid"], {"rec_name": name})
    _names.pop(u["uid"], None)
    return RedirectResponse("/me?msg=name-saved#notes", 303)


@app.post("/me/delete-notes")
def me_delete_notes(request: Request):
    u = current_user(request)
    if not u:
        return RedirectResponse("/", 303)
    n = 0
    for coll, field in ((nstore.verdicts, "uid"), (nstore.notes, "uid"), (nstore.judgments, "judge")):
        for doc in coll.by(field, u["uid"]):
            coll.delete(doc["id"])
            n += 1
    log.info("funnel notes-deleted uid=%s docs=%d", u["uid"], n)
    return RedirectResponse("/me?msg=notes-deleted", 303)


# ---------------- submitting a video that isn't listed ----------------
# Anyone signed in can submit a public YouTube video the catalogue doesn't have, with a review of it. It gets its own
# page (not indexed) and shows under New finds, but stays out of rankings, topics, search and sitemaps until a review
# of it counts with viewers; then it is vouched and indexable. Pay follows the usual rules: a partner's review earns
# only if it counts, only on pilot-topic videos, and never on a video they made or were paid to promote.
YT_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
_added_sync = {"t": -1e12}
_lookups = {}


def yt_videos(ids):
    """What YouTube says about up to 50 videos, {vid: item}. One quota unit per call; empty without a key."""
    ids = [i for i in ids if nts.VID_RE.match(i)][:50]
    if not YT_KEY or not ids:
        return {}
    r = httpx.get("https://www.googleapis.com/youtube/v3/videos", timeout=10,
                  params={"part": "snippet,contentDetails,status,statistics", "id": ",".join(ids), "key": YT_KEY})
    r.raise_for_status()
    return {it["id"]: it for it in r.json().get("items", [])}


def neighbours(text, n=25):
    """Catalogue videos whose titles are closest to this text, closest first, with how close they are."""
    sims = cat.title_emb @ cat.embed([text])[0]
    return [(cat.videos[int(i)], float(sims[int(i)])) for i in np.argsort(-sims)[:n]]


def guess_topic(title, channel):
    """The topic a submitted video's closest catalogue neighbours share most, weighted by how close they are."""
    votes = collections.Counter()
    for v, sim in neighbours(f"{title} {channel}"):
        for k, cid in enumerate(v["cats"][:2]):
            if cid in cat.cats:
                votes[cid] += max(sim, 0.0) * (1.0 if k == 0 else 0.5)
    return votes.most_common(1)[0][0] if votes else ""


def added_record(d):
    """A catalogue-style record for a submitted video, so cards, pages and reviews work unchanged."""
    v = {"vid": d["vid"], "title": d.get("title") or d["vid"], "channel": d.get("channel", ""),
         "channel_id": d.get("channel_id", ""), "dur": int(d.get("dur") or 0), "views": int(d.get("views") or 0),
         "published": d.get("published") or "", "cats": [d["topic"]] if d.get("topic") in cat.cats else [],
         "score": 0.0, "shelf": "evergreen", "type": "", "summary": "", "why_hn": "", "hn": {"recommenders": 0},
         "added": True, "added_by": d["uid"], "added_handle": d.get("handle", ""), "added_ts": float(d["ts"]),
         "vouched": d.get("status") == "vouched", "own": bool(d.get("own"))}
    v.update(slug=slugify(v["title"]) or v["vid"], dur_s=fmt_dur(v["dur"]), views_s=fmt_views(v["views"]),
             year=v["published"][:4])
    v["url"] = f"/v/{v['vid']}/{v['slug']}"
    return v


def vouch(vid):
    """A review of a submitted video counted with viewers: it is vouched for, and search engines may index it."""
    d = nstore.added.get(vid)
    if not d or d.get("status") != "new":
        return
    t = nts.clock()
    nstore.added.update(vid, {"status": "vouched", "vouched_ts": t})
    ADDED[vid] = added_record(dict(d, status="vouched"))
    log.info("funnel video-vouched vid=%s", vid)
    track_server("video_vouched", d["uid"], {"vid": vid, "hours": round((t - float(d["ts"])) / 3600, 1)})


def sync_added():
    """Keep this instance's overlay of submitted videos in step with the store: add new ones, drop removed ones and
    ones that have since joined the catalogue, vouch any whose review counted, and re-read YouTube's details when
    they are a week old (the API policies allow 30 days), dropping videos that are no longer public or playable."""
    _added_sync["t"] = nts.clock()
    docs = {d["vid"]: d for d in nstore.added.all() if d.get("vid")}
    stale = [vid for vid, d in docs.items() if d.get("status") in ("new", "vouched")
             and nts.clock() - float(d.get("fetched_ts") or 0) > nts.REFRESH_DAYS * 86400]
    if stale and YT_KEY:
        try:
            got = yt_videos(stale[:50])
            for vid in stale[:50]:
                if nts.check_video(got.get(vid)):
                    upd = {"status": "gone", "fetched_ts": nts.clock()}
                else:
                    upd = dict(nts.video_fields(got[vid]), fetched_ts=nts.clock())
                nstore.added.update(vid, upd)
                docs[vid].update(upd)
        except Exception as e:  # noqa: BLE001  try again next time
            log.info("submitted video refresh failed: %r", e)
    if any(d.get("status") == "new" for d in docs.values()):
        counted = {a["vid"] for a in all_assessed().values() if a["status"] == "qualified"}
        for vid, d in docs.items():
            if d.get("status") == "new" and vid in counted:
                vouch(vid)
                d["status"] = "vouched"
    keep = {vid: d for vid, d in docs.items() if d.get("status") in ("new", "vouched") and vid not in cat.by_vid}
    for vid in list(ADDED):
        if vid not in keep:
            ADDED.pop(vid, None)
    for vid, d in keep.items():
        ADDED[vid] = added_record(d)


def added_video_page(request, v, cats, crumbs, resume, resume_s, via_note, feat=None):
    first = next((n for n in sorted(nstore.notes.by("vid", v["vid"]), key=lambda n: float(n["ts"]))
                  if n.get("uid") == v["added_by"] and n.get("mod") == "ok"), None)
    adder = display_name(v["added_by"])
    desc = f"Added to HackerTube AI by {adder}" + (f": {first['text']}" if first else ".")
    ld = video_ld(v)
    ld["description"] = desc[:300]
    similar = [x for x, _ in neighbours(f"{v['title']} {v['channel']}", 13) if x["vid"] != v["vid"]][:12]
    return page(request, "video.html", {
        "v": v, "cats": cats, "mentions": [], "resume": resume, "resume_s": resume_s, "via_note": via_note, "co": [],
        "feat": feat,
        "similar": similar, "title": f"{v['title']} | Added to HackerTube AI by viewers", "description": desc[:300],
        "og_image": f"https://i.ytimg.com/vi/{v['vid']}/hqdefault.jpg", "jsonld": [ld, crumbs],
        "noindex": not v["vouched"], "adder": adder, "added_on": day(v["added_ts"]), "panel": nts.PANEL,
        "qualify": nts.QUALIFY, "submit_msg": request.query_params.get("msg", "")})


def lookup_ok(uid):
    """At most 60 YouTube lookups a day per person, so the submit form can't be used to burn API quota."""
    now_t = nts.clock()
    hist = [x for x in _lookups.get(uid, []) if now_t - x < 86400]
    _lookups[uid] = hist + [now_t]
    return len(hist) < 60


def submit_preview(u, url):
    """Look a link up for the submit form: (video preview, already-listed video, problem)."""
    vid, problem = nts.parse_video_link(url)
    if problem == "shorts":
        return None, None, "Shorts can't be submitted. Send a full video instead."
    if not vid:
        return None, None, "That doesn't look like a YouTube video link."
    if cat.get(vid):
        return None, cat.get(vid), ""
    if not lookup_ok(u["uid"]):
        return None, None, "That's a lot of lookups for one day. Try again tomorrow."
    try:
        item = yt_videos([vid]).get(vid)
    except Exception as e:  # noqa: BLE001
        log.info("submit lookup failed %s: %r", vid, e)
        return None, None, "YouTube didn't answer. Try again in a minute."
    err = nts.check_video(item)
    if err:
        return None, None, err
    f = nts.video_fields(item)
    topic = guess_topic(f["title"], f["channel"])
    pl = pilot()
    own = bool(u.get("yt_channel_id")) and u.get("yt_channel_id") == f["channel_id"]
    # a submission's own review is never paid; a partner earns a finder fee if it is vouched and later featured
    return dict(f, vid=vid, dur_s=fmt_dur(f["dur"]), views_s=fmt_views(f["views"]), topic=topic,
                topic_name=(cat.cats.get(topic) or {}).get("name", ""), own_channel=own, paid_here=False,
                finder=bool(pl) and hacker_status(u) == "approved" and not own
                and (pl["vids"] is None or pl["topic"] == topic)), None, ""


def submit_ctx(u, **kw):
    return dict({"title": "Submit a video", "noindex": True, "url": "", "video": None, "listed": None, "error": "",
                 "form": {}, "partner": hacker_status(u) == "approved", "pilot": pilot(), "panel": nts.PANEL,
                 "qualify": nts.QUALIFY, "min_mins": nts.SUBMIT_MIN_SECS // 60, "finder_fee": nts.money(nts.FINDER_CENTS),
                 "description": "Found a great video that isn't listed? Submit it with your review."}, **kw)


@app.get("/submit", response_class=HTMLResponse)
def submit_page(request: Request, url: str = ""):
    if not NOTES_ON:
        return page(request, "404.html", {"title": "Not found", "description": ""}, 404)
    u = current_user(request)
    url = (url or "").strip()[:300]
    if not (url and u):
        return page(request, "submit.html", submit_ctx(u, url=url))
    video, listed, error = submit_preview(u, url)
    return page(request, "submit.html", submit_ctx(u, url=url, video=video, listed=listed, error=error))


@app.post("/submit")
async def submit_post(request: Request, url: str = Form(""), stance: str = Form("rec"), text: str = Form(""),
                      needs: str = Form(""), moment: str = Form(""), seen: str = Form(""), own: str = Form(""),
                      did: str = Form("")):
    if not NOTES_ON:
        return RedirectResponse("/", 303)
    u = current_user(request)
    if not u:
        return RedirectResponse("/auth/login?next=/submit", 303)
    form = {"stance": stance, "text": text, "needs": needs, "moment": moment, "own": own == "1"}
    video, listed, error = await asyncio.to_thread(submit_preview, u, url)
    if listed:
        return RedirectResponse(listed["url"] + "?msg=listed", 303)

    def again(msg):
        return page(request, "submit.html", submit_ctx(u, url=url, video=video, error=msg, form=form))

    if error or not video:
        return again(error or "Something went wrong. Try again.")
    if stance not in nts.VERDICTS:
        return again("Say whether it's worth watching or one to skip.")
    if seen != "1":
        return again("Only submit videos you have watched.")
    text, needs = nts.clean_text(text), nts.clean_text(needs, nts.NEEDS_MAX)
    try:
        mom = nts.parse_moment(moment)
    except ValueError:
        return again("Write the moment as minutes:seconds, like 12:40.")
    err = nts.check_review(text, needs, mom, video["dur"])
    if err:
        return again(err)
    who, uid, did_h, net_h = acting(request, u, did)
    if over_limit("submit", nstore.added.by("uid", uid), net_h):
        return again("That's a lot of submissions for one day. Try again tomorrow.")
    mod, why = await asyncio.to_thread(nts.moderate, video["title"], "video title")
    if mod == "hidden":
        return again("That video can't be listed here.")
    is_own = own == "1" or video["own_channel"]
    paid = False  # the finder fee comes later, if it is vouched and picked for a weekly 10
    t = nts.clock()
    doc = {k: video[k] for k in ("vid", "title", "channel", "channel_id", "published", "dur", "views", "topic")}
    doc.update(uid=uid, handle=ids.handle(uid), ts=t, fetched_ts=t, status="new", own=is_own, mod=mod)
    if not nstore.added.add(video["vid"], doc):
        sync_added()
        v = cat.get(video["vid"])
        return RedirectResponse((v["url"] + "?msg=listed") if v else "/submit", 303)
    store.upsert(uid, {"handle": ids.handle(uid)})
    v = ADDED[video["vid"]] = added_record(doc)
    _, nmod = await asyncio.to_thread(add_note, v, who, uid, did_h, net_h, text, stance, watched=False, via="", paid=paid,
                                      review=True, needs=needs, moment=mom, extra={"own": is_own, "submitted": True})
    log.info("funnel video-submitted uid=%s vid=%s topic=%s paid=%s own=%s", uid, video["vid"], video["topic"], paid, is_own)
    track_server("video_submitted", uid, {"topic": video["topic"], "paid": paid, "own": is_own,
                                          "partner": hacker_status(u) == "approved", "stance": stance})
    track_server("review_created", uid, {"paid": paid, "partner": hacker_status(u) == "approved", "stance": stance,
                                         "mod": nmod, "chars": len(text), "needs": bool(needs), "moment": mom is not None,
                                         "submitted": True})
    return RedirectResponse(v["url"] + "?msg=submitted", 303)


if os.environ.get("DEV_LOGIN"):  # test environment only: demo Hackers, reviews and readers on one video
    @app.get("/dev/seed")
    def dev_seed(vid: str = ""):
        v = cat.get(vid) or next((x for x in cat.ranked if x["dur"] >= 600), cat.ranked[0])
        t0 = nts.clock() - 3600
        people = {"u-hacker-ada": "Ada", "u-hacker-lin": "Lin", "u-free-sam": "Sam"}
        for uid, name in people.items():
            store.upsert(uid, {"email": f"{uid}@example.com", "name": name, "rec_name": name, "handle": ids.handle(uid),
                               **({"hacker": {"status": "approved", "applied": now(), "decided": now()}} if "hacker" in uid else {})})
        store.upsert("u-hacker-ada", {"payout": {"paypal": "ada@example.com", "set": now()}})
        last = (datetime.datetime.fromtimestamp(nts.clock(), datetime.timezone.utc).replace(day=1)
                - datetime.timedelta(days=1)).strftime("%Y-%m")
        nstore.statements.add(f"{last}_u-hacker-ada", dict(nts.blank("u-hacker-ada"), month=last, kind="month", counted=6,
                                                            rated=7, first_ever=1, review_cents=3500, total_cents=3500,
                                                            finalised=now()))
        nstore.statements.add(f"{last}__closed", {"month": last, "marker": True, "partners": 2, "total_cents": 3500})
        nstore.payouts.add("u-hacker-ada_seed", {"uid": "u-hacker-ada", "cents": 1000, "ts": nts.clock() - 86400 * 20,
                                                 "method": "PayPal", "to": "ada@example.com", "ref": "seed", "by": "dev-admin"})
        def seed_note(uid, mins, text, stance, paid, needs="", moment=None):
            nstore.notes.add(nts.doc_id(v["vid"], uid), {
                "vid": v["vid"], "who": uid, "uid": uid, "handle": ids.handle(uid), "text": text, "stance": stance,
                "ts": t0 + mins * 60, "mod": "ok", "mod_why": "", "watched": not paid, "original": True, "sim": 0.0,
                "did_h": f"seed-d-{uid}", "net_h": f"seed-n-{uid}", "via": "", "paid": paid, "review": paid,
                "needs": needs, "moment": moment})
        seed_note("u-hacker-ada", 0, "The clearest first look at this for anyone who knows the basics but keeps getting the details wrong.",
                  nts.REC, True, "Comfort with the basics", 300)
        seed_note("u-hacker-lin", 5, "Worth it for working engineers who want the history behind the idea, not just the how-to.",
                  nts.REC, True, "", 900)
        seed_note("u-free-sam", 10, "Good, but skip it if you only want a quick overview: it takes its time getting started.",
                  nts.SKIP, False)
        votes = {"u-hacker-ada": ["agree"] * 4 + ["disagree"], "u-hacker-lin": ["agree"] * 4 + ["disagree"],
                 "u-free-sam": ["agree", "agree"]}
        for nid_uid, vs in votes.items():
            for i, vote in enumerate(vs):
                judge = f"u-reader-{nid_uid[-3:]}-{i}"
                nid = nts.doc_id(v["vid"], nid_uid)
                nstore.judgments.add(f"{nid}_{judge}", {"note_id": nid, "vid": v["vid"], "judge": judge, "judge_who": judge,
                                                        "vote": vote, "ts": t0 + 1200 + i * 60, "did_h": f"seed-d-{judge}",
                                                        "net_h": f"seed-n-{judge}"})
        return RedirectResponse(v["url"], 302)
