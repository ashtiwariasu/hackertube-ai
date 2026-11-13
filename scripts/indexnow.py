"""Submit every sitemap URL to IndexNow (Bing, Yandex, Seznam). Run after the domain is live or after a refresh."""
import logging, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
logging.basicConfig(level=logging.INFO)
from app.indexnow import submit_sitemap  # noqa: E402
print("submitted", submit_sitemap())
