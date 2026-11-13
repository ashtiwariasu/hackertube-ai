"""IndexNow: tell Bing, Yandex, Seznam and friends which URLs changed. One key, one endpoint, batches of 10k."""
import gzip, json, logging, os, re, urllib.request

log = logging.getLogger("hackertube.indexnow")
KEY = "888d9f608ebf739c8aed33aad92cd2ff"
HOST = "hackertube.ai"
ENDPOINT = "https://api.indexnow.org/indexnow"


def submit(urls):
    """Submit URLs in batches. Returns the number submitted; never raises (SEO is best effort)."""
    urls = [u for u in dict.fromkeys(urls) if u.startswith(f"https://{HOST}/")]
    done = 0
    for i in range(0, len(urls), 9000):
        batch = urls[i:i + 9000]
        body = json.dumps({"host": HOST, "key": KEY, "keyLocation": f"https://{HOST}/{KEY}.txt",
                           "urlList": batch}).encode()
        req = urllib.request.Request(ENDPOINT, data=body, headers={"Content-Type": "application/json; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                log.info("indexnow %s urls -> %s", len(batch), r.status)
                done += len(batch)
        except Exception as e:  # noqa: BLE001
            log.warning("indexnow batch failed: %s", e)
    return done


def sitemap_urls(base=f"https://{HOST}"):
    """Every URL in the live sitemap index (follows child sitemaps)."""
    def fetch(u):
        with urllib.request.urlopen(u, timeout=60) as r:
            return r.read().decode()
    top = fetch(f"{base}/sitemap.xml")
    children = re.findall(r"<sitemap>\s*<loc>([^<]+)</loc>", top)
    if not children:
        return re.findall(r"<loc>([^<]+)</loc>", top)
    out = []
    for c in children:
        out += re.findall(r"<loc>([^<]+)</loc>", fetch(c))
    return out


def submit_sitemap():
    try:
        return submit(sitemap_urls())
    except Exception as e:  # noqa: BLE001
        log.warning("indexnow sitemap submit failed: %s", e)
        return 0


SENT_BLOB = "indexnow/sent.txt.gz"   # every URL already submitted, one per line
WEEKLY_CAP = 10_000                   # stays well inside a 20,000-a-week budget


def pick_new(urls, sent, cap=WEEKLY_CAP):
    """URLs to submit this run: every page that is not a video page (hubs and topics change as the catalogue grows),
    then video pages never sent before, oldest sitemap order first, at most `cap` in all."""
    urls = [u for u in dict.fromkeys(urls) if u.startswith(f"https://{HOST}/")]
    hubs = [u for u in urls if "/v/" not in u]
    fresh = [u for u in urls if "/v/" in u and u not in sent]
    return (hubs + fresh)[:cap]


def submit_new(bucket=None):
    """Weekly: submit only what changed since the last run and remember it in GCS. Never raises."""
    try:
        from google.cloud import storage
        name = bucket or os.environ.get("DATA_BUCKET", "")
        if not name:
            log.info("indexnow: no DATA_BUCKET to remember what was sent; skipping")
            return 0
        b = storage.Client().bucket(name)
        blob = b.blob(SENT_BLOB)
        sent = set(gzip.decompress(blob.download_as_bytes()).decode().split()) if blob.exists() else set()
        todo = pick_new(sitemap_urls(), sent)
        n = submit(todo)
        if n:
            sent.update(todo[:n])
            blob.upload_from_string(gzip.compress("\n".join(sorted(sent)).encode()), content_type="application/gzip")
        log.info("indexnow: %s submitted, %s remembered", n, len(sent))
        return n
    except Exception as e:  # noqa: BLE001
        log.warning("indexnow submit_new failed: %s", e)
        return 0
