"""The first screen: the start row (one community pick and one open-web pick per field), the proof line,
and the 'Because you opened' row. Self-contained tiny catalogue, no dataset or embedding model needed."""
import json, os, sys, types

import numpy as np
import pytest
from jinja2 import Environment, FileSystemLoader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.catalog import Catalog  # noqa: E402
from app.web_source import derive_web_only  # noqa: E402


def comm_video(vid, title, cat_id, recommenders, score, views=500_000, web=None, shelf="evergreen", dur=1800):
    st = {"why": "", "recommenders": recommenders, "rec_months": 5, "mentions": 9, "first_year": 2014, "last_year": 2025, "score": score}
    v = {"vid": vid, "title": title, "channel": "Chan", "summary": title, "type": "talk", "level": "general", "shelf": shelf,
         "topic": title, "tags": [], "cats": [cat_id], "quote": None, "dur": dur, "views": views, "published": "2018-01-01",
         "channel_id": "UC1", "comm": {"hn": st}, "primary": "hn", "score": score, "recs": recommenders, "badge": "",
         "why_hn": "", "why_list": [], "hn": {"recommenders": recommenders, "rec_months": 5, "first_year": 2014, "last_year": 2025}}
    if web:
        v["web"] = web
    return v


def web_video(vid, title, area, kind, reputable, domains=40, curated=True, dur=1200):
    v = {"vid": vid, "title": title, "channel": "Chan", "summary": "", "type": "other", "level": "general", "shelf": "evergreen",
         "topic": "", "tags": [], "cats": [], "quote": None, "dur": dur, "views": 2_000_000, "published": "2015-01-01",
         "channel_id": "UC2", "comm": {},
         "web": {"domains": domains, "months": 1, "first_seen": "2026-10", "last_seen": "2026-10", "top_domains": ["mit.edu", "bbc.co.uk"],
                 "reputable": reputable, "curated": curated, "kind": kind, "area": area, "why": "Shows the thing."}}
    derive_web_only(v)
    return v


@pytest.fixture
def data_dir(tmp_path):
    vids = [comm_video("s1aaaaaaaaa", "Software talk", "web-development", 12, 9.0),
            comm_video("s2aaaaaaaaa", "Thin software talk", "web-development", 3, 9.5),        # under 8 recommenders: out
            comm_video("p1aaaaaaaaa", "Physics lecture", "mechanics", 20, 8.0),
            comm_video("nwaaaaaaaaa", "News clip", "mechanics", 30, 9.9, shelf="news"),          # news: out
            web_video("w1aaaaaaaaa", "Powers of Ten", "physics", "documentary", 37),
            web_video("w2aaaaaaaaa", "A music video", "software", "music_video", 70),          # not a starter kind: out
            web_video("w3aaaaaaaaa", "Web software lecture", "software", "lecture", 20),
            web_video("w4aaaaaaaaa", "Uncurated", "software", "talk", 90, curated=False)]      # not curated: out
    vids.sort(key=lambda v: -v["score"])
    d = tmp_path / "data"
    d.mkdir()
    json.dump(vids, open(d / "videos.json", "w"))
    for name in ("mentions", "related", "authors"):
        json.dump({}, open(d / f"{name}.json", "w"))
    json.dump({"areas": [{"id": "software", "name": "Software", "categories": [{"id": "web-development", "name": "Web development"}]},
                         {"id": "physics", "name": "Physics", "categories": [{"id": "mechanics", "name": "Mechanics"}]}]},
              open(d / "taxonomy.json", "w"))
    json.dump({"hn": {"label": "Hacker News", "short": "HN", "videos": 4}, "web": {"label": "The open web", "short": "Web", "videos": 3}},
              open(d / "communities.json", "w"))
    rng = np.random.default_rng(0)
    emb = rng.normal(size=(len(vids), 8)).astype(np.float32)
    emb /= np.linalg.norm(emb, axis=1, keepdims=True)
    np.save(d / "embeddings.npy", emb.astype(np.float16))
    np.save(d / "title_emb.npy", emb.astype(np.float16))
    return str(d)


def test_starter_alternates_community_and_web_picks_across_fields(data_dir):
    cat = Catalog(data_dir)
    vids = [v["vid"] for v in cat.starter(8, seed="d")]
    assert set(vids) == {"s1aaaaaaaaa", "p1aaaaaaaaa", "w1aaaaaaaaa", "w3aaaaaaaaa"}
    first_two = [cat.by_vid[x] for x in vids[:2]]
    assert first_two[0]["comm"] and not first_two[1]["comm"], "a community pick, then an open-web pick"
    areas = [cat.area_of_cat[first_two[0]["cats"][0]], first_two[1]["web"]["area"]]
    assert areas[0] != areas[1], "the first two picks come from different fields"


def test_starter_leaves_out_thin_news_uncurated_and_non_talk_videos(data_dir):
    vids = {v["vid"] for v in Catalog(data_dir).starter(8, seed="x")}
    assert not vids & {"s2aaaaaaaaa", "nwaaaaaaaaa", "w2aaaaaaaaa", "w4aaaaaaaaa"}


def test_starter_skips_what_the_visitor_hid_and_stays_unique(data_dir):
    cat = Catalog(data_dir)
    vids = [v["vid"] for v in cat.starter(8, seed="d", skip={"w1aaaaaaaaa"})]
    assert "w1aaaaaaaaa" not in vids and len(vids) == len(set(vids))


def test_proof_names_the_community_then_the_web(data_dir):
    cat = Catalog(data_dir)
    assert cat.proof(cat.by_vid["s1aaaaaaaaa"]) == "Recommended by 12 people on Hacker News since 2014"
    assert cat.proof(cat.by_vid["w1aaaaaaaaa"]) == "Linked by 40 websites, including mit.edu and bbc.co.uk"


def test_proof_joins_both_when_a_community_video_is_also_web_linked():
    cat = types.SimpleNamespace(comms={"hn": {"label": "Hacker News"}})
    v = comm_video("x", "t", "c", 9, 1.0, web={"domains": 98, "top_domains": ["cam.ac.uk"]})
    assert Catalog.proof(cat, v) == "Recommended by 9 people on Hacker News since 2014, linked by 98 websites"


def test_because_row_names_the_anchor_and_shows_proof_lines(data_dir):
    cat = Catalog(data_dir)
    env = Environment(loader=FileSystemLoader(os.path.join(ROOT, "app", "templates")), autoescape=True)
    vids = [cat.by_vid[x] for x in ("s1aaaaaaaaa", "p1aaaaaaaaa", "w1aaaaaaaaa", "w3aaaaaaaaa")]
    html = env.get_template("_because.html").render(anchor=cat.by_vid["w1aaaaaaaaa"], videos=vids,
                                                    reasons={v["vid"]: cat.proof(v) for v in vids}, likes=[], later=[])
    assert "Because you opened “Powers of Ten”" in html
    assert 'data-row="because"' in html
    assert html.count('class="reason"') == 4 and "Recommended by 20 people on Hacker News" in html


def test_a_web_only_video_with_a_topic_lands_on_that_topic_and_field(tmp_path, data_dir):
    vids = json.load(open(os.path.join(data_dir, "videos.json")))
    for v in vids:
        if v["vid"] == "w1aaaaaaaaa":
            v["cats"] = ["mechanics"]          # what web_topic_cats sets at build time
    json.dump(vids, open(os.path.join(data_dir, "videos.json"), "w"))
    cat = Catalog(data_dir)
    assert "w1aaaaaaaaa" in cat.cats["mechanics"]["vids"]
    assert cat.area_of(cat.by_vid["w1aaaaaaaaa"]) == "physics"


def test_area_of_prefers_the_gate_then_the_first_topic(data_dir):
    cat = Catalog(data_dir)
    assert cat.area_of(cat.by_vid["w3aaaaaaaaa"]) == "software"       # gate area, no topics
    assert cat.area_of(cat.by_vid["p1aaaaaaaaa"]) == "physics"        # community video: its topic's field


def test_web_view_is_ordered_by_reputable_sites_and_counted_by_field(data_dir):
    cat = Catalog(data_dir)
    reps = [v["web"].get("reputable", 0) for v in cat.web_order]
    assert cat.web_order and reps == sorted(reps, reverse=True)
    assert "w4aaaaaaaaa" not in {v["vid"] for v in cat.web_order}   # not curated, so not a member
    assert sum(cat.web_counts.values()) == sum(len(vs) for a, vs in cat.web_by_area.items() if a)
    for a, vs in cat.web_by_area.items():
        assert all(cat.area_of(v) == a for v in vs)


def test_open_web_membership_needs_three_minutes():
    cat = types.SimpleNamespace(web_curated=True, WEB_MIN_SECONDS=Catalog.WEB_MIN_SECONDS)
    long_v = web_video("l1aaaaaaaaa", "Long", "physics", "talk", 10, dur=181)
    short_v = web_video("s1aaaaaaaaa", "Short", "physics", "talk", 50, dur=150)
    assert Catalog.web_member(cat, long_v) and not Catalog.web_member(cat, short_v)
