"""Extract every question, answer and comment that links a YouTube video from one site archive.
Usage: python -I scripts/02_extract.py raw/<site>.7z work/
Streams Comments.xml, Posts.xml and Users.xml out of the 7z in one pass (nothing is written uncompressed) and writes
work/<site>/hits.jsonl (one line per (post or comment, video)) plus work/<site>/stats.json.

Watermark filter: drops the known fabricated posts (Ids 1000000001 / 1000000010, see
LunarWatcher/se-data-dump-transformer docs/meta/Known watermarks.md), any post or comment by the Community
user (-1), and counts any other post with Id >= 1e9 (suspicious, also dropped).
"""
import html, json, os, re, subprocess, sys, collections

arch, workdir = sys.argv[1], sys.argv[2]
host = os.path.basename(arch)[:-3]                       # cooking.stackexchange.com
short = host.replace(".stackexchange.com", "")
if short.endswith(".com") or short.endswith(".net"):
    short = short[:-4]                                    # stackoverflow, superuser, es.stackoverflow, mathoverflow
community = "se:" + short
out_dir = os.path.join(workdir, host)
os.makedirs(out_dir, exist_ok=True)

WATERMARK_IDS = {1000000001, 1000000010}
QUICK = re.compile(rb"(?i)youtu")
ATTR = re.compile(r'(\w+)="([^"]*)"')
# a YouTube URL inside text (scheme optional); stops at whitespace, quotes, angle brackets, closing parens/brackets
YT_URL = re.compile(r'(?i)(?<![\w.@/-])(?:https?:)?(?://)?(?:[\w-]+\.)*(?:youtube(?:-nocookie)?\.com|youtu\.be)/[^\s"\'<>\]\[)(]*')
ANY_URL = re.compile(r'(?i)\b(?:https?://|www\.)[^\s"\'<>\]\[)(]+')
VID = r"([A-Za-z0-9_-]{11})(?![A-Za-z0-9_-])"
VID_PATTERNS = [re.compile(p, re.I) for p in (
    r"youtu\.be/" + VID,
    r"/(?:embed|v|e|shorts|live)/" + VID,
    r"[?&#!]v=" + VID,
    r"[?&]vi?=" + VID,
    r"(?:%3F|%26)v(?:%3D|=)" + VID,
)]


def vids_in_url(url):
    u = html.unescape(html.unescape(url))
    found = []
    for p in VID_PATTERNS:
        for m in p.finditer(u):
            if m.group(1) not in found:
                found.append(m.group(1))
    return found[:1]          # one video per URL (playlist URLs keep the first, the v= one)


def attrs(line):
    return {k: html.unescape(v) for k, v in ATTR.findall(line)}


def stream_all():
    """One decompression pass: Comments.xml, Posts.xml and Users.xml come out of the (solid) 7z in archive order
    on one stdout stream. Yields (section, line) where section is the XML root element of the current file."""
    p = subprocess.Popen(["7zz", "x", "-so", "-mmt=on", arch, "Comments.xml", "Posts.xml", "Users.xml"],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=1 << 22)
    section = None
    for line in p.stdout:
        if line.startswith(b"  <row"):
            yield section, line
            continue
        t = line.strip().lstrip(b"\xef\xbb\xbf")
        for name in (b"comments", b"posts", b"users"):
            if t.startswith(b"<" + name + b">") or t == b"<" + name + b">":
                section = name.decode()
    p.wait()
    if p.returncode:
        raise SystemExit(f"7zz failed {p.returncode}")


# ---------- text cleaning ----------
A_TAG = re.compile(r'(?is)<a\s[^>]*?href\s*=\s*"([^"]*)"[^>]*>(.*?)</a>')
TAGS = re.compile(r"(?s)<[^>]+>")
BLOCKQUOTE = re.compile(r"(?is)<blockquote>.*?</blockquote>")
MD_LINK = re.compile(r"\[([^\]]{0,300})\]\(\s*([^)\s]+)[^)]*\)")
WS = re.compile(r"\s+")
URLISH = re.compile(r"(?i)^\s*(?:https?://|www\.|youtu|\S+\.\S+/\S*$)")


def link_token(url, vid):
    vs = vids_in_url(url) if YT_URL.search(url) or "youtu" in url.lower() else []
    if vid in vs:
        return "\x00V\x00"
    return "\x00L\x00"


def finish(s):
    s = html.unescape(s)
    s = WS.sub(" ", s).strip()
    return s


def clean_html(body, vid, drop_quotes=False):
    if drop_quotes:
        body = BLOCKQUOTE.sub(" ", body)
    body = re.sub(r"(?i)</?(p|br|li|h\d|pre|blockquote|div)[^>]*>", " ", body)

    def a_sub(m):
        href, inner = m.group(1), TAGS.sub("", m.group(2))
        tok = link_token(href, vid)
        inner_t = html.unescape(inner).strip()
        if not inner_t or URLISH.match(inner_t):
            return f" {tok} "
        return f" {inner_t} {tok} "
    body = A_TAG.sub(a_sub, body)
    body = TAGS.sub(" ", body)
    body = html.unescape(body)
    body = YT_URL.sub(lambda m: f" {link_token(m.group(0), vid)} ", body)
    body = ANY_URL.sub(" \x00L\x00 ", body)
    return finish_tokens(body)


def clean_comment(text, vid):
    text = html.unescape(text)

    def md_sub(m):
        inner, url = m.group(1).strip(), m.group(2)
        tok = link_token(url, vid)
        if not inner or URLISH.match(inner):
            return f" {tok} "
        return f" {inner} {tok} "
    text = MD_LINK.sub(md_sub, text)
    text = YT_URL.sub(lambda m: f" {link_token(m.group(0), vid)} ", text)
    text = ANY_URL.sub(" \x00L\x00 ", text)
    text = TAGS.sub(" ", text)
    return finish_tokens(text)


def finish_tokens(s):
    s = WS.sub(" ", s).strip()
    return s.replace("\x00V\x00", "[VIDEO]").replace("\x00L\x00", "[LINK]")


def own_len(s):
    return len(WS.sub(" ", s.replace("[VIDEO]", " ").replace("[LINK]", " ")).strip())


CODE_HTML = re.compile(r"(?is)<pre[^>]*>.*?</pre>|<code[^>]*>.*?</code>")
CODE_MD = re.compile(r"(?s)`[^`]*`")
NOT_IDS = {"videoseries", "live_stream", "user_upload"}     # /embed/videoseries?list=..., /embed/live_stream?channel=...


def all_vids(text, is_html):
    """Video ids linked in prose. Links inside code (<pre>, <code>, `backticks`) are sample URLs in code, not
    pointers to a video, so they are ignored."""
    text = CODE_HTML.sub(" ", text) if is_html else CODE_MD.sub(" ", html.unescape(text))
    vs = []
    for m in YT_URL.finditer(html.unescape(text)):
        for v in vids_in_url(m.group(0)):
            if v not in vs and v not in NOT_IDS:
                vs.append(v)
    return vs


stats = collections.Counter()

# ---------- one pass: comments, then posts, then users (archive order is alphabetical) ----------
ID_RE = re.compile(rb'<row Id="(\d+)" PostTypeId="(\d+)"(?: AcceptedAnswerId="\d+")?(?: ParentId="(\d+)")?')
TITLE_RE = re.compile(rb' Title="([^"]*)"')
UID_RE = re.compile(rb'<row Id="(-?\d+)"')
DN_RE = re.compile(rb' DisplayName="([^"]*)"')
comment_hits = []          # (attrs, vids)
need_posts = set()
titles = {}                # question id -> escaped title (bytes)
parent_of = {}             # answer id -> question id (only for answers we need)
post_hits = []
names = {}
need_b = None
for section, raw in stream_all():
    if section == "comments":
        stats["comments_total"] += 1
        if not QUICK.search(raw):
            continue
        a = attrs(raw.decode("utf-8", "replace"))
        vids = all_vids(a.get("Text", ""), False)
        if not vids:
            continue
        stats["comments_with_yt"] += 1
        if a.get("UserId") == "-1":
            stats["dropped_community_user"] += 1
            continue
        comment_hits.append((a, vids))
        need_posts.add(int(a["PostId"]))
    elif section == "posts":
        m = ID_RE.search(raw, 0, 200)
        if not m:
            continue
        pid, ptype = int(m.group(1)), m.group(2)
        stats["posts_total"] += 1
        if ptype == b"1":
            t = TITLE_RE.search(raw)
            if t:
                titles[pid] = t.group(1)
        elif ptype == b"2" and m.group(3):
            if pid in need_posts:
                parent_of[pid] = int(m.group(3))
        if ptype not in (b"1", b"2") or not QUICK.search(raw):
            continue
        a = attrs(raw.decode("utf-8", "replace"))
        vids = all_vids(a.get("Body", ""), True)
        if not vids:
            continue
        stats["posts_with_yt_q" if ptype == b"1" else "posts_with_yt_a"] += 1
        if pid in WATERMARK_IDS or pid >= 1_000_000_000:
            stats["dropped_watermark"] += 1
            continue
        if a.get("OwnerUserId") == "-1":
            stats["dropped_community_user"] += 1
            continue
        if ptype == b"2":
            parent_of[pid] = int(a.get("ParentId", 0))
        post_hits.append((a, vids))
    elif section == "users":
        if need_b is None:
            need_b = {a["OwnerUserId"].encode() for a, _ in post_hits if a.get("OwnerUserId")}
            need_b |= {a["UserId"].encode() for a, _ in comment_hits if a.get("UserId")}
        m = UID_RE.search(raw, 0, 60)
        if m and m.group(1) in need_b:
            d = DN_RE.search(raw)
            names[m.group(1).decode()] = html.unescape(d.group(1).decode("utf-8", "replace")) if d else ""
for k in ("comments_total", "posts_total"):
    if not stats[k]:
        raise SystemExit(f"no rows for {k}: archive layout not as expected")


def title_of(qid):
    t = titles.get(qid)
    return html.unescape(t.decode("utf-8", "replace")) if t else ""


def author_of(uid, dname):
    if uid:
        return f"u{uid}", names.get(uid) or f"user{uid}"
    if dname:
        return "n:" + dname, dname
    return None, None


out = open(os.path.join(out_dir, "hits.jsonl"), "w", encoding="utf-8")
n_out = 0
for a, vids in post_hits:
    pid = int(a["Id"])
    is_q = a.get("PostTypeId") == "1"
    akey, aname = author_of(a.get("OwnerUserId"), a.get("OwnerDisplayName"))
    if not akey:
        stats["dropped_no_author"] += 1
        continue
    qid = pid if is_q else parent_of.get(pid, 0)
    for v in vids:
        text = clean_html(a.get("Body", ""), v)
        own = own_len(clean_html(a.get("Body", ""), v, drop_quotes=True))
        out.write(json.dumps({
            "id": f"{community}:{'q' if is_q else 'a'}{pid}", "kind": "question" if is_q else "answer",
            "url": f"https://{host}/{'q' if is_q else 'a'}/{pid}", "vid": v, "community": community, "site": host,
            "author_key": akey, "author": aname, "month": a.get("CreationDate", "")[:7],
            "context": title_of(qid), "score": int(a.get("Score", 0) or 0), "own_len": own, "text": text[:4000],
            "n_videos": len(vids)}, ensure_ascii=False) + "\n")
        n_out += 1
for a, vids in comment_hits:
    cid, post = int(a["Id"]), int(a["PostId"])
    akey, aname = author_of(a.get("UserId"), a.get("UserDisplayName"))
    if not akey:
        stats["dropped_no_author"] += 1
        continue
    qid = post if post in titles else parent_of.get(post, 0)
    for v in vids:
        text = clean_comment(a.get("Text", ""), v)
        out.write(json.dumps({
            "id": f"{community}:c{cid}", "kind": "comment",
            "url": f"https://{host}/posts/comments/{cid}", "vid": v, "community": community, "site": host,
            "author_key": akey, "author": aname, "month": a.get("CreationDate", "")[:7],
            "context": title_of(qid), "score": int(a.get("Score", 0) or 0), "own_len": own_len(text), "text": text[:4000],
            "n_videos": len(vids)}, ensure_ascii=False) + "\n")
        n_out += 1
out.close()
stats["hit_lines"] = n_out
stats["questions"] = len(titles)
json.dump({"host": host, "community": community, **stats}, open(os.path.join(out_dir, "stats.json"), "w"))
open(os.path.join(out_dir, "DONE"), "w").close()
print(host, dict(stats), flush=True)
