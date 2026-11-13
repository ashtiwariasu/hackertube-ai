"""Refresh job (Cloud Run Job): pull the source bundle from GCS, refresh HN mentions and YouTube metadata,
rebuild the catalogue, publish a new release, and push the updated bundle back.
Run: python -m app.refresh [--skip-hn]"""
import datetime, logging, os, subprocess, sys, tarfile

from . import datarelease

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("hackertube.refresh")
WORK = "/tmp/work"


def sh(cmd, **kw):
    log.info("$ %s", " ".join(cmd))
    subprocess.run(cmd, check=True, cwd=WORK, **kw)


def write_viewers_pack():
    """Vouched submissions become packs/viewers for this build, so they reach search, topics and sitemaps.
    A failure here never stops the refresh: the build runs with last week's pack instead."""
    try:
        from google.cloud import firestore
        from . import viewers_pack
        rows = viewers_pack.from_firestore(firestore.Client(project=os.environ.get("GCP_PROJECT") or None))
        viewers_pack.write(os.path.join(WORK, "packs", viewers_pack.COMMUNITY), rows)
        log.info("viewers pack: %d vouched submissions", len(rows))
    except Exception:  # noqa: BLE001
        log.exception("viewers pack failed; building without this week's changes")


def main():
    from google.cloud import storage
    b = storage.Client().bucket(datarelease.BUCKET)
    os.makedirs(WORK, exist_ok=True)
    b.blob("source/bundle.tgz").download_to_filename("/tmp/bundle.tgz")
    with tarfile.open("/tmp/bundle.tgz") as t:
        t.extractall(WORK, filter="data")
    os.makedirs(os.path.join(WORK, "app/data"), exist_ok=True)
    import shutil
    shutil.copy(os.path.join(os.path.dirname(__file__), "data", "taxonomy.json"), os.path.join(WORK, "app/data/taxonomy.json"))
    write_viewers_pack()
    env = dict(os.environ)
    env["HT_APP_ROOT"] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # build_catalog.py imports app.web_source
    if "--skip-hn" not in sys.argv and os.path.exists(os.path.join(WORK, "packs/hn-fresh/refresh_hn.py")):
        try:
            sh([sys.executable, "-I", "packs/hn-fresh/refresh_hn.py"], env=env)
        except subprocess.CalledProcessError:
            log.exception("HN refresh failed; continuing with metadata refresh")
    sh([sys.executable, "-I", "scripts/build_catalog.py", "--refresh-meta"], env=env)
    release = datetime.datetime.utcnow().strftime("%Y%m%d-%H%M")
    datarelease.publish(release, os.path.join(WORK, "app/data"))
    with tarfile.open("/tmp/bundle-new.tgz", "w:gz") as t:
        for p in ("scripts", "packs", "data"):
            t.add(os.path.join(WORK, p), arcname=p)
    b.blob("source/bundle.tgz").upload_from_filename("/tmp/bundle-new.tgz")
    log.info("refresh done: release %s", release)
    from . import indexnow
    log.info("indexnow: %s urls submitted", indexnow.submit_new())  # only URLs new since the last run


if __name__ == "__main__":
    main()
