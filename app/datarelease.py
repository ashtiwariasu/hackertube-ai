"""Catalogue releases in GCS: the refresh job publishes a new app/data set; running instances pick it up.
Layout: gs://<bucket>/catalog/LATEST (text: release id) and gs://<bucket>/catalog/<release>/<files>."""
import logging, os, shutil, threading, time

log = logging.getLogger("hackertube.release")
BUCKET = os.environ.get("DATA_BUCKET", "")
FILES = ["videos.json", "mentions.json", "related.json", "authors.json", "taxonomy.json", "communities.json",
         "embeddings.npy", "title_emb.npy"]
CHECK_EVERY = 1800
_state = {"checked": 0.0, "release": os.environ.get("BAKED_RELEASE", "baked"), "busy": False}


def _client():
    from google.cloud import storage
    return storage.Client()


def latest_release():
    if not BUCKET:
        return None
    blob = _client().bucket(BUCKET).blob("catalog/LATEST")
    return blob.download_as_text().strip() if blob.exists() else None


def fetch_release(release, dest):
    b = _client().bucket(BUCKET)
    tmp = dest + ".part"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    for f in FILES:
        blob = b.blob(f"catalog/{release}/{f}")
        if blob.exists():
            blob.download_to_filename(os.path.join(tmp, f))
    shutil.rmtree(dest, ignore_errors=True)
    os.rename(tmp, dest)
    return dest


def publish(release, src_dir):
    b = _client().bucket(BUCKET)
    for f in FILES:
        p = os.path.join(src_dir, f)
        if os.path.exists(p):
            b.blob(f"catalog/{release}/{f}").upload_from_filename(p)
    b.blob("catalog/LATEST").upload_from_string(release)
    log.info("published release %s", release)


def discard(data_dir):
    """Delete a downloaded release once it is loaded. On Cloud Run /tmp is memory, and nothing reads the files again.
    Only release folders this module made are touched, never the data baked into the image."""
    if data_dir and os.path.basename(os.path.normpath(data_dir)).startswith("catalog-") and data_dir.startswith("/tmp/"):
        shutil.rmtree(data_dir, ignore_errors=True)
        log.info("discarded %s", data_dir)


def startup_dir(baked_dir):
    """At boot: use the newest published release if there is one, else the data baked into the image."""
    try:
        r = latest_release()
        if r and r != _state["release"]:
            d = fetch_release(r, f"/tmp/catalog-{r}")
            _state["release"] = r
            log.info("booting with release %s", r)
            return d
    except Exception:  # noqa: BLE001
        log.exception("release check failed at startup; using baked data")
    _state["checked"] = time.time()
    return baked_dir


def maybe_reload(on_new):
    """Called on requests; at most every 30 min, checks for a newer release and swaps it in on a thread."""
    if not BUCKET or _state["busy"] or time.time() - _state["checked"] < CHECK_EVERY:
        return
    _state["busy"], _state["checked"] = True, time.time()

    def work():
        try:
            r = latest_release()
            if r and r != _state["release"]:
                d = fetch_release(r, f"/tmp/catalog-{r}")
                on_new(d)
                _state["release"] = r
                log.info("swapped to release %s", r)
        except Exception:  # noqa: BLE001
            log.exception("release reload failed")
        finally:
            _state["busy"] = False
    threading.Thread(target=work, daemon=True).start()


def current():
    return _state["release"]
