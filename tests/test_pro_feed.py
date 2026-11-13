"""The Pro feed through the real app: a person's own world on the first screen, each pick with its reason, and
one liked video on a first visit never deciding the whole feed. In-memory store, full catalogue and model."""
import os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.update({"DEV_LOGIN": "1", "STORE": "memory", "PILOT_TOPIC": "*", "ADMIN_UIDS": "admin", "HT_NOTES": "1",
                   "WATCH_SCALE": "1"})   # the same settings as test_notes_api, whichever imports the app first
os.environ.pop("DATA_BUCKET", None)

from fastapi.testclient import TestClient  # noqa: E402

from app import main as M  # noqa: E402


def signed_in(uid):
    """The user document as a request sees it (current_user adds the id)."""
    return dict(M.store.get(uid), uid=uid)


def busiest_channel():
    ch = max(M.cat.channel_ids, key=lambda c: len(M.cat.channel_ids[c]))
    return ch, M.cat.get(M.cat.channel_ids[ch][0])["channel"]


def make_person(uid, pro):
    c = TestClient(M.app)
    c.get(f"/dev/login?uid={uid}&pro={pro}", follow_redirects=False)
    ch_id, _ = busiest_channel()
    topics = ["business strategy", "entrepreneurship and innovation", "economics"]
    u = M.store.get(uid)
    u["uid"] = uid
    liked = next(v for v in M.cat.ranked if v["channel_id"] != ch_id)["vid"]
    M.save_profile(u, {"sources": {"aiprofile": {"connected_at": "2026-11-09T00:00:00+00:00",
                                                 "profile": {"topics": topics, "learning": [], "work": [], "format": "short"}}},
                       "vecs": {"aiprofile": M.cat.text_vector(topics)}, "yt_channels_matched": [ch_id], "likes": [liked]})
    return c, liked


def test_the_pro_feed_starts_with_who_you_follow_and_says_why_for_every_pick():
    c, liked = make_person("feed-pat", 1)
    _, ch_name = busiest_channel()
    d = M.personal_data(signed_in("feed-pat"))
    first = d["feed"][:12]
    assert first[0]["channel"] == ch_name and d["reasons"][first[0]["vid"]] == f"From {ch_name}, which you follow"
    assert all(d["reasons"].get(v["vid"]) for v in first)
    assert sum(r.startswith("For your interest in") for r in (d["reasons"][v["vid"]] for v in first)) >= 4
    assert liked not in [v["vid"] for v in d["feed"]]
    assert sum(v["channel"] == ch_name for v in first) == 1         # one familiar creator never fills the screen
    page = c.get("/me").text
    assert "which you follow" in page and "For your interest in business strategy" in page


def test_a_free_person_sees_the_reasons_on_their_preview_too():
    c, _ = make_person("feed-fay", 0)
    assert "For your interest in" in c.get("/").text


def test_the_feed_is_remembered_until_something_that_shapes_it_changes():
    c, _ = make_person("feed-kim", 1)
    first = M.personal_data(signed_in("feed-kim"))
    assert M.personal_data(signed_in("feed-kim")) is first
    top = first["feed"][0]["vid"]
    assert c.post("/api/signals/merge", json={"hidden": [top]}).json()["ok"]
    after = M.personal_data(signed_in("feed-kim"))
    assert after is not first and top not in [v["vid"] for v in after["feed"]]
