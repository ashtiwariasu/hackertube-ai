"""The open-web source: how many independent websites linked a video across how many monthly crawls.

A web pack (`packs/web/videos.jsonl`, format in packs/PACKS.md) carries one row per video. The build merges
it into the catalogue as `v["web"]`, alongside the per-community `v["comm"]` entries from HN and Stack Exchange.
This module holds the pure pieces shared by `scripts/build_catalog.py` and `app/catalog.py` so they can be tested
without the raw dataset."""
import json, math, os, re

WEB_COMM = "web"
WEB_LABEL = "The open web"
WEB_SHORT = "Web"
WEB_FIELDS = ("domains", "months", "first_seen", "last_seen", "top_domains")
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
VID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def parse_web_row(row):
    """Validate one pack row and return the catalogue `web` record, or raise ValueError."""
    vid = row.get("vid")
    if not isinstance(vid, str) or not VID_RE.match(vid):
        raise ValueError(f"bad vid {vid!r}")
    out = {}
    for k in ("domains", "months"):
        n = row.get(k)
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ValueError(f"{vid}: {k} must be a positive integer, got {n!r}")
        out[k] = n
    for k in ("first_seen", "last_seen"):
        m = row.get(k)
        if not isinstance(m, str) or not MONTH_RE.match(m):
            raise ValueError(f"{vid}: {k} must be YYYY-MM, got {m!r}")
        out[k] = m
    if out["first_seen"] > out["last_seen"]:
        raise ValueError(f"{vid}: first_seen {out['first_seen']} is after last_seen {out['last_seen']}")
    doms = row.get("top_domains") or []
    if not isinstance(doms, list) or not all(isinstance(d, str) and d for d in doms):
        raise ValueError(f"{vid}: top_domains must be a list of hostnames")
    out["top_domains"] = [d.strip().lower() for d in doms][:5]
    rep = row.get("reputable", 0)
    if isinstance(rep, bool) or not isinstance(rep, int) or rep < 0:
        raise ValueError(f"{vid}: reputable must be a non-negative integer, got {rep!r}")
    out["reputable"] = rep          # linking sites in the Tranco top 100k (0 when the pack does not say)
    out["curated"] = bool(row.get("curated", False))   # passed the length, category and title rules of phase B
    why = (row.get("why") or "").strip()
    if why:
        out["why"] = why
    for k in ("kind", "area"):  # the phase W gate's call: what sort of video it is and which taxonomy area it belongs to
        x = row.get(k)
        if isinstance(x, str) and x.strip() and len(x) <= 40:
            out[k] = x.strip().lower()
    topics = row.get("topics")  # W3: one or two taxonomy topic ids picked within that area
    if isinstance(topics, list):
        t = [x.strip().lower() for x in topics if isinstance(x, str) and x.strip() and len(x) <= 60][:2]
        if t:
            out["topics"] = t
    return out


def load_web_pack(path):
    """Read `<pack>/videos.jsonl` (or the file itself) into {vid: web record}. Missing file -> {}."""
    if os.path.isdir(path):
        path = os.path.join(path, "videos.jsonl")
    if not os.path.exists(path):
        return {}
    out = {}
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as e:
                raise ValueError(f"{path}:{n}: not JSON ({e})") from None
            try:
                rec = parse_web_row(row)
            except ValueError as e:
                raise ValueError(f"{path}:{n}: {e}") from None
            prev = out.get(row["vid"])
            if prev is None or (rec["domains"], rec["months"]) > (prev["domains"], prev["months"]):
                out[row["vid"]] = rec
    return out


def web_score(web, has_other_sources):
    """Ranking contribution of the web signal.

    With an HN or SE source it is a small bonus like the Wikipedia one (at most 1.5), so community
    recommendations keep deciding the order. A web-only video gets that bonus plus half a point per crawl
    month (at most 3), which lands it below most 3-month community videos."""
    bonus = 0.5 * min(math.log2(1 + web["domains"]), 3)
    if has_other_sources:
        return round(bonus, 2)
    return round(bonus + 0.5 * min(web["months"], 6), 2)


def web_only(v):
    """True for a catalogue video whose only source is the open web."""
    return bool(v.get("web")) and not v.get("comm")


def apply_web(videos, web, make_record):
    """Attach web records to the catalogue dict {vid: record} before the derived fields are computed.

    `make_record(vid)` builds a base record (YouTube metadata, empty `comm`) for a video not yet in the
    catalogue, or returns None when the video is unusable (deleted, private, not embeddable).
    The web pack alone admits a video only when it is curated (the judge kept it and it is in English):
    link counts on their own are mostly music, trailers and embeds. Videos a community already vouched
    for take the web record whatever it says.
    Returns the number of videos added by the web pack alone."""
    added = 0
    for vid, rec in web.items():
        if vid not in videos:
            if not rec.get("curated"):
                continue
            base = make_record(vid)
            if base is None:
                continue
            videos[vid] = base
            added += 1
        videos[vid]["web"] = {k: rec[k] for k in rec}
    return added


def web_topic_cats(v, valid):
    """A web-only video takes the gate's topics as its categories, so it appears on those topic pages.
    Videos that already have categories (from a community card) keep them."""
    if v.get("cats"):
        return
    topics = [t for t in ((v.get("web") or {}).get("topics") or []) if t in valid]
    if topics:
        v["cats"] = topics[:1]  # the first pick only: 70% match known topics, the second adds 8 points and more noise


def derive_web_only(v):
    """Fill the derived fields a web-only video needs so templates, ranking and feeds treat it like any other."""
    web = v["web"]
    v["primary"] = WEB_COMM
    v["score"] = web_score(web, has_other_sources=False)
    v["recs"] = 0
    v["badge"] = ""
    v["why_list"] = []
    v["why_hn"] = web.get("why", "")
    v["hn"] = {"recommenders": 0, "rec_months": 0, "first_year": None, "last_year": None}
