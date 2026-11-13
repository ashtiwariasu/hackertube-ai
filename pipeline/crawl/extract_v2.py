"""v2: per (page, video) emit  vid \t domain \t kind \t pagetype \t anchor_text
kind: A (hyperlink in page text), IFRAME/EMBED (player embed), OTHER. pagetype: disc if the page URL looks like a
forum/thread/comment/blog post, else ''. Reads a WAT gzip stream on stdin."""
import gzip, re, sys, os
from urllib.parse import urlsplit
LINK = re.compile(rb'\{"path":"([A-Za-z]+)@/[a-z-]+","url":"([^"]*youtu[^"]*)"(?:,"[a-z]+":"[^"]*")*?(?:,"text":"((?:[^"\\]|\\.){0,160})")?[^}]*\}')
VID = re.compile(rb'(?:youtube(?:-nocookie)?\.com/(?:watch\?(?:[^"&\\\s]*&(?:amp;)?)*v=|embed/|shorts/|v/|live/)|youtu\.be/)([A-Za-z0-9_-]{11})')
DISC = re.compile(r'forum|thread|/t/|topic|comment|discuss|/post|blog|community|/q/|question|/r/|/wiki', re.I)
SKIP = {b"videoseries", b"live_stream"}
rows = set(); cur = b""; pages = 0
with gzip.open(sys.stdin.buffer, 'rb') as f:
    for line in f:
        if line.startswith(b"WARC-Target-URI:"):
            cur = line[16:].strip(); pages += 1
        elif line.startswith(b'{"Container"') and b"youtu" in line:
            line = line.replace(b"\\/", b"/")
            try: dom = urlsplit(cur.decode('latin-1')).hostname or ""
            except Exception: dom = ""
            if dom.startswith("www."): dom = dom[4:]
            ptype = "disc" if DISC.search(cur.decode('latin-1', 'ignore')) else ""
            for tag, url, text in LINK.findall(line):
                m = VID.search(url)
                if not m or m.group(1) in SKIP: continue
                kind = "A" if tag == b"A" else ("EMBED" if tag in (b"IFRAME", b"EMBED", b"OBJECT", b"VIDEO", b"SOURCE") else "OTHER")
                t = text.decode('utf-8', 'ignore').replace("\t", " ").replace("\\n", " ").strip()[:120] if kind == "A" else ""
                rows.add((m.group(1).decode(), dom, kind, ptype, t))
out = sys.argv[1]
with open(out + ".tmp", "w") as o:
    for r in rows: o.write("\t".join(r) + "\n")
os.replace(out + ".tmp", out)
print(pages, len(rows))
