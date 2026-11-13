"""W4b capacity fixes: half-precision embeddings that behave like float32, and release folders deleted once loaded."""
import os, sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import datarelease  # noqa: E402
from app.catalog import HalfMatrix  # noqa: E402


@pytest.fixture
def mats():
    rng = np.random.default_rng(3)
    a = rng.normal(size=(40_000, 16)).astype(np.float32)
    a /= np.linalg.norm(a, axis=1, keepdims=True)
    return a.astype(np.float16).astype(np.float32), HalfMatrix(a)


def test_half_matrix_matches_float32_for_every_operation_the_app_uses(mats):
    full, half = mats
    v, q = full[7], full[:5]
    assert np.allclose(half @ v, full @ v, atol=1e-3)                     # similar, search, personal
    assert np.allclose(q @ half.T, q @ full.T, atol=1e-3)                 # match_titles
    assert np.allclose(half @ q.T, full @ q.T, atol=1e-3)
    assert np.allclose(half[7], full[7]) and half[7].dtype == np.float32  # one row
    assert np.allclose(half[[1, 9, 3]] @ v, full[[1, 9, 3]] @ v, atol=1e-3)
    assert (half @ v).dtype == np.float32 and len(half) == 40_000 and half.shape == (40_000, 16)
    assert np.argsort(-(half @ v))[0] == 7
    assert half.nbytes == full.nbytes // 2


def test_discard_deletes_only_downloaded_release_folders(tmp_path, monkeypatch):
    rel = tmp_path / "catalog-20261104-1200"
    rel.mkdir(); (rel / "videos.json").write_text("[]")
    baked = tmp_path / "data"
    baked.mkdir(); (baked / "videos.json").write_text("[]")
    datarelease.discard(str(baked))
    assert baked.exists()
    datarelease.discard(str(rel))
    assert rel.exists() == (not str(rel).startswith("/tmp/"))  # only /tmp release folders are removed
    real = "/tmp/catalog-test-discard"
    os.makedirs(real, exist_ok=True)
    open(f"{real}/videos.json", "w").write("[]")
    datarelease.discard(real)
    assert not os.path.exists(real)
    datarelease.discard(None)


def test_indexnow_sends_hubs_every_time_and_video_pages_once():
    from app.indexnow import pick_new
    urls = ["https://hackertube.ai/", "https://hackertube.ai/topics/optics", "https://hackertube.ai/v/aaaaaaaaaaa/a",
            "https://hackertube.ai/v/bbbbbbbbbbb/b", "https://other.example/v/x", "https://hackertube.ai/v/aaaaaaaaaaa/a"]
    first = pick_new(urls, set())
    assert first == ["https://hackertube.ai/", "https://hackertube.ai/topics/optics",
                     "https://hackertube.ai/v/aaaaaaaaaaa/a", "https://hackertube.ai/v/bbbbbbbbbbb/b"]
    again = pick_new(urls, set(first))
    assert again == ["https://hackertube.ai/", "https://hackertube.ai/topics/optics"]
    assert len(pick_new([f"https://hackertube.ai/v/{i:011d}/x" for i in range(50)], set(), cap=10)) == 10
