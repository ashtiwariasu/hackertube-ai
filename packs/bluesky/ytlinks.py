"""Shared helpers: find YouTube video ids in a Bluesky post record."""
import re
from urllib.parse import urlsplit, parse_qs, unquote

VID_RE = re.compile(r'^[A-Za-z0-9_-]{11}$')
# URL-ish spans in free text (display text may be truncated, e.g. "youtu.be/dQw4w9WgXcQ?si=ab...")
TEXT_URL_RE = re.compile(
    r'(?:https?://)?(?:[a-z0-9-]+\.)*(?:youtube\.com|youtube-nocookie\.com|youtu\.be)/[^\s<>"\'\]\)]*',
    re.I)
YT_HOSTS = ('youtube.com', 'youtube-nocookie.com', 'youtu.be')


def _host_ok(host):
    host = (host or '').lower().split(':')[0]
    return any(host == h or host.endswith('.' + h) for h in YT_HOSTS)


def vid_from_url(u):
    """Return an 11-char video id for a YouTube video URL, else None."""
    if not u:
        return None
    u = u.strip()
    if not re.match(r'^[a-z]+://', u, re.I):
        u = 'https://' + u
    try:
        p = urlsplit(u)
    except ValueError:
        return None
    if not _host_ok(p.hostname):
        return None
    host = p.hostname.lower()
    parts = [x for x in p.path.split('/') if x]
    cand = None
    if host.endswith('youtu.be'):
        cand = parts[0] if parts else None
    else:
        q = parse_qs(p.query)
        if parts and parts[0] in ('watch', 'watch_popup') or not parts:
            cand = (q.get('v') or [None])[0]
        elif parts[0] in ('shorts', 'live', 'embed', 'v', 'e') and len(parts) > 1:
            cand = parts[1]
        elif parts[0] == 'attribution_link':
            inner = unquote((q.get('u') or [''])[0])
            return vid_from_url('https://www.youtube.com' + inner) if inner.startswith('/') else None
    if not cand:
        return None
    cand = cand[:11] if len(cand) >= 11 else cand  # display text may glue chars after the id
    return cand if VID_RE.match(cand) else None


def vids_in_text(text):
    out = []
    for m in TEXT_URL_RE.finditer(text or ''):
        v = vid_from_url(m.group(0))  # ids shorter than 11 chars (cut-off display text) are rejected
        if v:
            out.append(v)
    return out


def _embed_external(embed):
    if not isinstance(embed, dict):
        return None
    t = embed.get('$type', '')
    if t == 'app.bsky.embed.external':
        return embed.get('external') or None
    if t == 'app.bsky.embed.recordWithMedia':
        return _embed_external(embed.get('media'))
    return None


def extract(record):
    """Return (vids, facet_links, embed_ext) for a post record.
    vids: ordered unique ids; facet_links: [[byteStart, byteEnd, uri], ...];
    embed_ext: {'uri','title','description'} or None."""
    vids = []
    links = []
    text = record.get('text') or ''
    for f in record.get('facets') or []:
        if not isinstance(f, dict):
            continue
        idx = f.get('index') or {}
        for feat in f.get('features') or []:
            if isinstance(feat, dict) and feat.get('$type') == 'app.bsky.richtext.facet#link':
                uri = feat.get('uri') or ''
                links.append([idx.get('byteStart'), idx.get('byteEnd'), uri[:500]])
                v = vid_from_url(uri)
                if v:
                    vids.append(v)
    ext = _embed_external(record.get('embed'))
    if ext:
        v = vid_from_url(ext.get('uri'))
        if v:
            vids.append(v)
    vids.extend(vids_in_text(text))
    seen, uniq = set(), []
    for v in vids:
        if v not in seen:
            seen.add(v)
            uniq.append(v)
    emb = None
    if ext:
        emb = {'uri': (ext.get('uri') or '')[:500], 'title': (ext.get('title') or '')[:300],
               'description': (ext.get('description') or '')[:500]}
    return uniq, links, emb


def quick_maybe_yt(raw):
    """Cheap prefilter on the raw frame."""
    return 'youtu' in raw or 'YouTu' in raw or 'YOUTU' in raw
