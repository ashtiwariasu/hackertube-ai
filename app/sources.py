"""Connectable interest sources. Every fetcher reads public data over a public API and
returns {"handle", "label", "texts", "weight", ...}. None needs an approval from anyone."""
import asyncio, collections, hashlib, html, ipaddress, logging, os, re, socket
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

log = logging.getLogger("hackertube.sources")
UA = "HackerTubeAI/1.0 (+https://hackertube.ai/about; builds a read-only interest profile)"
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
SE_KEY = os.environ.get("STACKEXCHANGE_KEY", "")
DISCORD_CLIENT_ID = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET = os.environ.get("DISCORD_CLIENT_SECRET", "")

HANDLE_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,62})$")
DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
TAG_RE = re.compile(r"<[^>]+>")


class SourceError(Exception):
    """A message safe to show the user."""


def strip_html(s):
    return html.unescape(TAG_RE.sub(" ", s or "")).replace("\xa0", " ").strip()


def squash(s):
    return re.sub(r"\s+", " ", s or "").strip()


def last_segment(h):
    h = (h or "").strip().lstrip("@")
    if "://" in h or "/" in h:
        h = [p for p in h.split("?")[0].split("/") if p][-1]
    return h.lstrip("@")


def public_host(host):
    """Refuse hosts that resolve to private, loopback or link-local addresses."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise SourceError("That address doesn't resolve.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise SourceError("That address isn't reachable from here.")


async def fetch_public(client, url, max_bytes=1_200_000, hops=4):
    """GET a user-supplied URL with redirect-by-redirect host checks and a size cap."""
    for _ in range(hops):
        u = urlparse(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise SourceError("Only http and https links work here.")
        await asyncio.to_thread(public_host, u.hostname)
        async with client.stream("GET", url, headers={"User-Agent": UA, "Accept": "text/html,application/xml,*/*"},
                                 follow_redirects=False) as r:
            if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                url = urljoin(url, r.headers["location"])
                continue
            if r.status_code != 200:
                raise SourceError(f"That page answered with status {r.status_code}.")
            buf = b""
            async for chunk in r.aiter_bytes():
                buf += chunk
                if len(buf) >= max_bytes:
                    break
            return url, buf, r.headers.get("content-type", "")
    raise SourceError("Too many redirects.")


class PageText(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "form", "iframe"}
    BLOCK = {"h1", "h2", "h3", "h4", "p", "li", "blockquote", "td", "dd", "summary", "figcaption"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.feeds, self.title, self.desc = [], [], "", ""
        self._skip, self._title = 0, False

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._title = True
        elif tag == "meta" and not self.desc and (a.get("name", "").lower() in ("description", "og:description")
                                                  or a.get("property", "").lower() == "og:description"):
            self.desc = a.get("content", "")
        elif tag == "link" and "alternate" in a.get("rel", "").lower() and ("rss" in a.get("type", "") or "atom" in a.get("type", "")):
            self.feeds.append(a.get("href", ""))
        elif tag in self.BLOCK or tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._title = False
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)

    def lines(self):
        seen, out = set(), []
        for line in "".join(self.parts).split("\n"):
            line = squash(line)
            if len(line) >= 25 and line not in seen:
                seen.add(line)
                out.append(line[:300])
        return out


def parse_feed(raw):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    items = []
    ns = "{http://www.w3.org/2005/Atom}"
    for it in root.iter("item"):
        t = squash((it.findtext("title") or ""))
        d = squash(strip_html(it.findtext("description") or ""))[:300]
        if t:
            items.append(f"{t}. {d}".strip())
    for it in root.iter(f"{ns}entry"):
        t = squash(it.findtext(f"{ns}title") or "")
        d = squash(strip_html((it.findtext(f"{ns}summary") or it.findtext(f"{ns}content") or "")))[:300]
        if t:
            items.append(f"{t}. {d}".strip())
    return items[:60]


# ---------------- fetchers ----------------
def gh_headers():
    h = {"User-Agent": UA, "Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        h["Authorization"] = f"Bearer {GITHUB_TOKEN}"
    return h


async def github(client, handle):
    h = last_segment(handle)
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})", h):
        raise SourceError("That doesn't look like a GitHub username.")
    r = await client.get(f"https://api.github.com/users/{h}", headers=gh_headers())
    if r.status_code == 404:
        raise SourceError("No GitHub user with that name.")
    if r.status_code in (403, 429):
        raise SourceError("GitHub is rate-limiting us for a few minutes. Try again shortly.")
    r.raise_for_status()
    u = r.json()
    texts, langs, stars = [], collections.Counter(), 0
    if u.get("bio"):
        texts.append(squash(u["bio"]))
    if u.get("company"):
        texts.append(f"Works at {squash(u['company'])}")
    for page in (1, 2):
        rs = await client.get(f"https://api.github.com/users/{h}/starred",
                              params={"per_page": 100, "page": page}, headers=gh_headers())
        if rs.status_code != 200:
            break
        items = rs.json()
        for repo in items:
            stars += 1
            if repo.get("language"):
                langs[repo["language"]] += 1
            texts.append(squash(f"{repo['full_name']}: {repo.get('description') or ''} "
                                f"{' '.join(repo.get('topics') or [])} {repo.get('language') or ''}"))
        if len(items) < 100:
            break
    rr = await client.get(f"https://api.github.com/users/{h}/repos",
                          params={"per_page": 100, "sort": "pushed"}, headers=gh_headers())
    if rr.status_code == 200:
        for repo in rr.json():
            if repo.get("fork"):
                continue
            if repo.get("language"):
                langs[repo["language"]] += 2
            texts.append(squash(f"{repo['name']}: {repo.get('description') or ''} {repo.get('language') or ''}"))
    top = [l for l, _ in langs.most_common(4)]
    label = f"{stars} starred repos" + (f" · {', '.join(top)}" if top else "")
    return {"handle": u.get("login", h), "label": label, "texts": texts, "weight": 1.0,
            "website": (u.get("blog") or "").strip()}


async def hackernews(client, handle, cat):
    h = last_segment(handle)
    if not re.fullmatch(r"[A-Za-z0-9_-]{2,15}", h):
        raise SourceError("That doesn't look like a Hacker News username.")
    r = await client.get(f"https://hacker-news.firebaseio.com/v0/user/{h}.json", headers={"User-Agent": UA})
    if r.status_code != 200 or r.json() is None:
        raise SourceError("No Hacker News user with that name.")
    u = r.json()
    texts = []
    if u.get("about"):
        texts.append(squash(strip_html(u["about"]))[:500])
    rc = await client.get("https://hn.algolia.com/api/v1/search_by_date",
                          params={"tags": f"comment,author_{h}", "hitsPerPage": 200}, headers={"User-Agent": UA})
    comments = rc.json().get("hits", []) if rc.status_code == 200 else []
    for hit in comments:
        t = squash(strip_html(hit.get("comment_text") or ""))[:300]
        if len(t) > 40:
            texts.append(f"{squash(hit.get('story_title') or '')}: {t}")
    rs = await client.get("https://hn.algolia.com/api/v1/search_by_date",
                          params={"tags": f"story,author_{h}", "hitsPerPage": 100}, headers={"User-Agent": UA})
    stories = rs.json().get("hits", []) if rs.status_code == 200 else []
    texts += [squash(s["title"]) for s in stories if s.get("title")]
    recs = cat.author_recs(h)
    label = f"{u.get('karma', 0):,} karma · {len(comments)} comments read"
    if recs:
        label += f" · you recommended {len(recs)} of these talks yourself"
    return {"handle": h, "label": label, "texts": texts, "weight": 1.0, "catalog_vids": recs}


async def bluesky(client, handle):
    h = last_segment(handle).lower()
    if not DOMAIN_RE.match(h):
        raise SourceError("Bluesky handles look like name.bsky.social.")
    base = "https://public.api.bsky.app/xrpc/"
    r = await client.get(base + "app.bsky.actor.getProfile", params={"actor": h}, headers={"User-Agent": UA})
    if r.status_code != 200:
        raise SourceError("No Bluesky account with that handle.")
    p = r.json()
    texts = [squash(p["description"])] if p.get("description") else []
    rf = await client.get(base + "app.bsky.feed.getAuthorFeed",
                          params={"actor": h, "limit": 100, "filter": "posts_no_replies"}, headers={"User-Agent": UA})
    posts = []
    if rf.status_code == 200:
        for it in rf.json().get("feed", []):
            t = squash((it.get("post") or {}).get("record", {}).get("text", ""))
            if len(t) > 30:
                posts.append(t[:300])
    rg = await client.get(base + "app.bsky.graph.getFollows", params={"actor": h, "limit": 100},
                          headers={"User-Agent": UA})
    follows = rg.json().get("follows", []) if rg.status_code == 200 else []
    texts += posts
    texts += [squash(f"{f.get('displayName', '')}: {f.get('description', '')}")[:200] for f in follows if f.get("description")]
    return {"handle": h, "label": f"{len(posts)} posts · {len(follows)} follows", "texts": texts, "weight": 0.8}


async def mastodon(client, handle):
    h = (handle or "").strip().lstrip("@")
    if "://" in h:
        u = urlparse(h)
        h = f"{u.path.strip('/').lstrip('@')}@{u.hostname or ''}"
    if "@" not in h:
        raise SourceError("Mastodon handles look like name@instance.social.")
    user, inst = h.rsplit("@", 1)
    inst = inst.lower()
    if not DOMAIN_RE.match(inst) or not HANDLE_RE.match(user):
        raise SourceError("Mastodon handles look like name@instance.social.")
    await asyncio.to_thread(public_host, inst)
    r = await client.get(f"https://{inst}/api/v1/accounts/lookup", params={"acct": user}, headers={"User-Agent": UA})
    if r.status_code != 200:
        raise SourceError("Couldn't find that account on that server.")
    a = r.json()
    texts = [squash(strip_html(a.get("note", "")))]
    texts += [squash(f"{f.get('name', '')}: {strip_html(f.get('value', ''))}") for f in a.get("fields", [])]
    rs = await client.get(f"https://{inst}/api/v1/accounts/{a['id']}/statuses",
                          params={"limit": 40, "exclude_replies": "true", "exclude_reblogs": "true"},
                          headers={"User-Agent": UA})
    posts = [squash(strip_html(s.get("content", ""))) for s in rs.json()] if rs.status_code == 200 else []
    texts += [p[:300] for p in posts if len(p) > 30]
    return {"handle": f"{user}@{inst}", "label": f"{len(posts)} posts", "texts": [t for t in texts if t], "weight": 0.8}


async def stackoverflow(client, handle):
    m = re.search(r"users/(\d+)", handle or "") or re.fullmatch(r"\s*(\d+)\s*", handle or "")
    if not m:
        raise SourceError("Paste your profile link, like stackoverflow.com/users/12345/name.")
    uid = m.group(1)
    params = {"site": "stackoverflow"}
    if SE_KEY:
        params["key"] = SE_KEY
    r = await client.get(f"https://api.stackexchange.com/2.3/users/{uid}", params=params, headers={"User-Agent": UA})
    items = r.json().get("items", []) if r.status_code == 200 else []
    if not items:
        raise SourceError("No Stack Overflow user with that id.")
    u = items[0]
    rt = await client.get(f"https://api.stackexchange.com/2.3/users/{uid}/tags",
                          params={**params, "pagesize": 60, "order": "desc", "sort": "popular"}, headers={"User-Agent": UA})
    tags = rt.json().get("items", []) if rt.status_code == 200 else []
    rq = await client.get(f"https://api.stackexchange.com/2.3/users/{uid}/questions",
                          params={**params, "pagesize": 50, "order": "desc", "sort": "votes"}, headers={"User-Agent": UA})
    questions = rq.json().get("items", []) if rq.status_code == 200 else []
    texts = []
    for t in tags:
        texts += [f"Stack Overflow: {t['name']}"] * min(int(t.get("count", 1)), 3)
    texts += [squash(strip_html(q.get("title", ""))) for q in questions]
    top = [t["name"] for t in tags[:4]]
    label = f"{int(u.get('reputation', 0)):,} reputation" + (f" · {', '.join(top)}" if top else "")
    return {"handle": uid, "label": label, "texts": texts, "weight": 1.0}


async def website(client, url):
    url = (url or "").strip()
    if url and "://" not in url:
        url = "https://" + url
    final, raw, ctype = await fetch_public(client, url)
    texts, title, feed_items = [], "", []
    if "xml" in ctype or raw.lstrip()[:5] in (b"<?xml", b"<rss ", b"<feed"):
        feed_items = parse_feed(raw)
    else:
        p = PageText()
        p.feed(raw.decode("utf-8", "ignore"))
        title = squash(p.title)
        if title:
            texts.append(title)
        if p.desc:
            texts.append(squash(p.desc))
        texts += p.lines()[:60]
        for href in p.feeds[:2]:
            try:
                _, fraw, _ = await fetch_public(client, urljoin(final, href), max_bytes=600_000)
                feed_items += parse_feed(fraw)
            except (SourceError, httpx.HTTPError):
                continue
    texts += feed_items
    if not texts:
        raise SourceError("Couldn't find any readable text on that page.")
    host = urlparse(final).hostname or ""
    label = (title[:60] if title else host) + (f" · {len(feed_items)} posts" if feed_items else "")
    return {"handle": url, "label": label, "texts": texts, "weight": 1.0}


async def email_lookup(client, email):
    """Public profiles that advertise this email: Gravatar, then GitHub's public-email search."""
    e = (email or "").strip().lower()
    found = {"github": "", "website": "", "bio": "", "name": ""}
    if not e:
        return found
    h = hashlib.sha256(e.encode()).hexdigest()
    try:
        r = await client.get(f"https://api.gravatar.com/v3/profiles/{h}", headers={"User-Agent": UA})
        if r.status_code == 200:
            p = r.json()
            found["name"] = squash(p.get("display_name") or "")
            found["bio"] = squash(p.get("description") or "")[:300]
            for acc in (p.get("verified_accounts") or []):
                url = acc.get("url") or ""
                if "github.com/" in url and not found["github"]:
                    found["github"] = last_segment(url)
            for lnk in (p.get("links") or []):
                url = lnk.get("url") or ""
                if url.startswith("http") and "gravatar.com" not in url and not found["website"]:
                    found["website"] = url
    except httpx.HTTPError:
        pass
    if not found["github"]:
        try:
            rg = await client.get("https://api.github.com/search/users", params={"q": f"{e} in:email"},
                                  headers=gh_headers())
            if rg.status_code == 200 and rg.json().get("total_count") == 1:
                found["github"] = rg.json()["items"][0]["login"]
        except httpx.HTTPError:
            pass
    return found


# ---------------- Discord (optional; needs DISCORD_CLIENT_ID/SECRET) ----------------
def discord_enabled():
    return bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET)


def discord_auth_url(redirect_uri, state):
    from urllib.parse import urlencode
    return "https://discord.com/oauth2/authorize?" + urlencode({
        "client_id": DISCORD_CLIENT_ID, "redirect_uri": redirect_uri, "response_type": "code",
        "scope": "identify guilds", "state": state, "prompt": "consent"})


async def discord_guilds(client, code, redirect_uri):
    tok = (await client.post("https://discord.com/api/oauth2/token", data={
        "client_id": DISCORD_CLIENT_ID, "client_secret": DISCORD_CLIENT_SECRET, "grant_type": "authorization_code",
        "code": code, "redirect_uri": redirect_uri})).json()
    access = tok.get("access_token")
    if not access:
        raise SourceError("Discord didn't complete the sign-in.")
    r = await client.get("https://discord.com/api/users/@me/guilds", headers={"Authorization": f"Bearer {access}"})
    if r.status_code != 200:
        raise SourceError("Couldn't read your Discord servers.")
    names = [squash(g.get("name", "")) for g in r.json() if g.get("name")]
    return {"handle": "discord", "label": f"{len(names)} servers", "texts": [f"Discord server: {n}" for n in names],
            "weight": 0.6}
