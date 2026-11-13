"""The personal shortlist (Catalog.librarian): someone's own world first, every pick with its
reason, popularity only breaking ties. Self-contained tiny catalogue with hand-made embeddings, no model needed."""
import json, os, sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import catalog as C  # noqa: E402
from app.catalog import Catalog  # noqa: E402
from app.web_source import derive_web_only  # noqa: E402

DIM = 12
BUSINESS, AI, ANIME, CODE = 0, 1, 2, 3
_noise = np.random.default_rng(7)


def axis(k, spread=0.25):
    """A unit vector mostly along axis k, with its own small wobble in the spare dimensions."""
    v = np.zeros(DIM, dtype=np.float32)
    v[k] = 1.0
    v[4:] = _noise.normal(0, spread, DIM - 4)
    return v / np.linalg.norm(v)


def video(vid, title, channel, emb, dur=1500, score=4.0, web=False):
    v = {"vid": vid, "title": title, "channel": channel, "summary": title, "type": "talk", "level": "general",
         "shelf": "evergreen", "topic": title, "tags": [], "cats": ["general"], "quote": None, "dur": dur,
         "views": 50_000, "published": "2020-01-01", "channel_id": "UC" + channel, "_emb": emb}
    if web:
        v.update(comm={}, web={"domains": 6, "months": 1, "first_seen": "2026-10", "last_seen": "2026-10",
                                "top_domains": ["example.org"], "reputable": 0, "curated": True, "kind": "talk",
                                "area": "business", "why": title})
        derive_web_only(v)
    else:
        st = {"why": "", "recommenders": 5, "rec_months": 3, "mentions": 6, "first_year": 2016, "last_year": 2025, "score": score}
        v.update(comm={"hn": st}, primary="hn", score=score, recs=5, badge="5 HN recs", why_hn="", why_list=[],
                 hn={"recommenders": 5, "rec_months": 3, "first_year": 2016, "last_year": 2025})
    return v


def videos():
    out = [video(f"biz{k:08d}", f"Business talk {k}", f"Biz{k}", axis(BUSINESS)) for k in range(10)]
    out += [video(f"fol{k:08d}", f"Podcast episode {k}", f"Followed{k}", axis(AI), dur=4800) for k in range(5)]
    out += [video(f"ais{k:08d}", f"AI explainer {k}", f"AI{k}", axis(AI)) for k in range(6)]
    out += [video(f"ani{k:08d}", f"Anime short {k}", f"Anime{k}", axis(ANIME, 0.05)) for k in range(3)]
    out += [video("popular0000", "Everyone's favourite coding talk", "Famous", axis(CODE), score=30.0)]
    same = axis(BUSINESS, 0.02)
    out += [video("hnpair00000", "Strategy, recommended", "Pair1", same), video("webpair0000", "Strategy, linked", "Pair2", same, web=True)]
    out += [video("clip0000000", "Strategy in ninety seconds", "Clips", axis(BUSINESS, 0.02), dur=90),
            video("longbiz0000", "Strategy, the four-hour cut", "Marathon", axis(BUSINESS, 0.02), dur=4800),
            video("midbiz00000", "Strategy, the twenty-minute cut", "Midway", axis(BUSINESS, 0.02), dur=1200)]
    return out


@pytest.fixture
def cat(tmp_path):
    vids = videos()
    d = tmp_path / "data"
    d.mkdir()
    emb = np.stack([v.pop("_emb") for v in vids]).astype(np.float16)
    json.dump(vids, open(d / "videos.json", "w"))
    for name in ("mentions", "related", "authors"):
        json.dump({}, open(d / f"{name}.json", "w"))
    json.dump({"areas": [{"id": "all", "name": "All", "categories": [{"id": "general", "name": "General"}]}]},
              open(d / "taxonomy.json", "w"))
    json.dump({"hn": {"label": "Hacker News", "short": "HN"}}, open(d / "communities.json", "w"))
    np.save(d / "embeddings.npy", emb)
    np.save(d / "title_emb.npy", emb)
    c = Catalog(str(d))
    c.embed = lambda texts: (_ for _ in ()).throw(RuntimeError("no model in tests"))
    return c


def person(c, liked=None, fmt=""):
    """A business-and-AI person who follows five AI podcasts and liked one anime short on a first visit."""
    vec = axis(BUSINESS, 0) + axis(AI, 0) + 0.3 * axis(ANIME, 0)
    anchors = [("topic", "For your interest in business strategy", axis(BUSINESS, 0), 0.0, ""),
               ("topic", "For your interest in AI", axis(AI, 0), 0.0, "")]
    if liked:
        x = c.by_vid[liked]
        anchors.append(("item", f"Because you liked “{x['title']}”", c.emb[x["i"]], -0.83, liked))
    followed = [f"UCFollowed{k}" for k in range(5)]
    return c.librarian(vec / np.linalg.norm(vec), anchors, followed=followed, fmt=fmt, n=60)


def test_creators_you_follow_lead_the_first_screen_but_never_fill_it(cat):
    feed, why = person(cat)
    first = feed[:C.LIB_FIRST]
    familiar = [k for k, v in enumerate(first) if v["channel"].startswith("Followed")]
    assert familiar == list(C.LIB_FOLLOWED_AT)                       # slots 1, 4 and 8: recognisable, not all of it
    assert why[first[0]["vid"]] == f"From {first[0]['channel']}, which you follow"
    assert len({v["channel"] for v in first}) == len(first)          # no channel twice on the first screen


def test_popularity_alone_never_gets_a_video_in(cat):
    feed, _ = person(cat)
    vids = [v["vid"] for v in feed]
    assert "popular0000" not in vids[:C.LIB_FIRST]
    assert all(vids.index(v["vid"]) < vids.index("popular0000") for v in feed if v["title"].startswith("Business talk"))


def test_every_pick_says_why_it_is_there(cat):
    feed, why = person(cat)
    assert all(why.get(v["vid"]) for v in feed)
    assert any(why[v["vid"]] == "For your interest in business strategy" for v in feed[:C.LIB_FIRST])


def test_one_liked_video_earns_one_pick_and_is_never_picked_itself(cat):
    feed, why = person(cat, liked="ani00000000")
    vids = [v["vid"] for v in feed]
    assert "ani00000000" not in vids
    assert sum(v["channel"].startswith("Anime") for v in feed[:C.LIB_FIRST]) <= 1
    because = [v for v in feed if why[v["vid"]].startswith("Because you liked")]
    assert because and all(v["channel"].startswith("Anime") for v in because)


def test_a_community_recommendation_beats_a_link_count_when_the_match_is_equal(cat):
    vids = [v["vid"] for v in person(cat)[0]]
    assert vids.index("hnpair00000") < vids.index("webpair0000")


def test_clips_and_lengths_someone_said_they_avoid_move_down(cat):
    vids = [v["vid"] for v in person(cat, fmt="short")[0]]
    assert vids.index("midbiz00000") < vids.index("longbiz0000")
    assert vids.index("midbiz00000") < vids.index("clip0000000")
    # what someone already follows they watch at its own length: the hour-long podcasts keep their slots
    first = person(cat, fmt="short")[0][:C.LIB_FIRST]
    assert sum(v["channel"].startswith("Followed") for v in first) == len(C.LIB_FOLLOWED_AT)
