"""Funnel and traffic stats from Cloud Run request logs. No third-party analytics, no cookies.
Usage: .venv/bin/python -I scripts/stats.py [hours]   (default 24)"""
import collections, json, os, re, subprocess, sys
from urllib.parse import urlparse


def setting(name):
    """A value from the environment, else from deploy.env (plain NAME=value lines)."""
    if os.environ.get(name):
        return os.environ[name]
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "deploy.env")
    for line in open(path) if os.path.exists(path) else ():
        k, _, v = line.strip().partition("=")
        if k == name and v and not v.startswith("$"):
            return v.strip().strip('"')
    raise SystemExit(f"set {name} in the environment or deploy.env")

hours = int(sys.argv[1]) if len(sys.argv) > 1 else 24
base = f'resource.type="cloud_run_revision" AND resource.labels.service_name="{setting("SERVICE")}"'

def read(filter_, limit=20000):
    out = subprocess.run(["gcloud", "logging", "read", filter_, "--project", setting("PROJECT"), "--freshness", f"{hours}h",
                          "--limit", str(limit), "--format", "json"], capture_output=True, text=True, check=True).stdout
    return json.loads(out or "[]")

BOT = re.compile(r"bot|crawl|spider|slurp|curl|python-requests|Go-http|HeadlessChrome|facebookexternalhit|preview|Nexus 5X Build/MMB29P|Google-|^node$|^$", re.I)
SKIP = re.compile(r"^/(static/|favicon|healthz|api/|stripe/|.*\.txt$|sitemap)")

reqs = read(base + ' AND httpRequest.requestUrl:"/"')
paths, refs, ips, bots = collections.Counter(), collections.Counter(), set(), 0
for e in reqs:
    h = e.get("httpRequest") or {}
    url, ua = h.get("requestUrl", ""), h.get("userAgent", "")
    path = urlparse(url).path or "/"
    if SKIP.match(path):
        continue
    if BOT.search(ua):
        bots += 1
        continue
    paths[path] += 1
    ips.add(h.get("remoteIp"))
    ref = h.get("referer", "")
    host = urlparse(ref).netloc
    if host and "hackertube" not in host:
        refs[host] += 1

funnel = collections.Counter()
for e in read(base + ' AND textPayload:"funnel "', 5000):
    m = re.search(r"funnel ([a-z-]+)(?: .*?source=([a-z]+))?", e.get("textPayload", ""))
    if m:
        funnel[m.group(1) + (f":{m.group(2)}" if m.group(2) else "")] += 1

print(f"Last {hours}h")
print(f"Human page views: {sum(paths.values())}   distinct IPs: {len(ips)}   bot hits skipped: {bots}")
print("\nTop pages"); [print(f"  {n:5d}  {p}") for p, n in paths.most_common(15)]
print("\nReferrers"); [print(f"  {n:5d}  {r}") for r, n in refs.most_common(15)] or print("  none")
print("\nFunnel"); [print(f"  {n:5d}  {k}") for k, n in sorted(funnel.items())] or print("  no events")
