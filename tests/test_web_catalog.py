"""A catalogue with an open-web source: loading, feeds, the Communities entry, and the rendered badge and line.

Self-contained: builds its own tiny data directory and renders the Jinja templates directly, so neither
tests/conftest.py, the raw dataset, nor the embedding model is needed."""
import json, os, sys, types

import numpy as np
import pytest
from jinja2 import Environment, FileSystemLoader

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.catalog import Catalog  # noqa: E402
from app.web_source import derive_web_only  # noqa: E402

HN = {"why": "HN keeps linking it.", "recommenders": 7, "rec_months": 5, "mentions": 9, "first_year": 2016, "last_year": 2025, "score": 6.5}
WEB_B = {"domains": 30, "months": 4, "first_seen": "2026-03", "last_seen": "2026-10", "top_domains": ["dev.to", "lobste.rs"],
         "why": "Course pages link it as the standard explanation."}
WEB_C = {"domains": 1, "months": 1, "first_seen": "2026-05", "last_seen": "2026-05", "top_domains": []}


def video(vid, title, comm, **extra):
    v = {"vid": vid, "title": title, "channel": "Chan", "summary": f"About {title}.", "type": "talk", "level": "general",
         "shelf": "evergreen", "topic": title.lower(), "tags": [], "cats": ["web-development"], "quote": None, "dur": 1500,
         "views": 10_000, "published": "2020-05-01", "channel_id": "UC1", "comm": comm}
    v.update(extra)
    if comm:
        v.update({"primary": "hn", "score": 6.5, "recs": 7, "badge": "7 HN recs", "why_hn": HN["why"],
                  "why_list": [{"c": "hn", "label": "Hacker News", **HN}],
                  "hn": {"recommenders": 7, "rec_months": 5, "first_year": 2016, "last_year": 2025}})
    else:
        derive_web_only(v)
    return v


@pytest.fixture
def data_dir(tmp_path):
    vids = [video("aaaaaaaaaaa", "Plain HN talk", {"hn": dict(HN)}),
            video("bbbbbbbbbbb", "Linked everywhere", {"hn": dict(HN)}, web=dict(WEB_B)),
            video("ccccccccccc", "Web only clip", {}, web=dict(WEB_C), summary="", cats=[])]
    vids.sort(key=lambda v: -v["score"])
    d = tmp_path / "data"
    d.mkdir()
    json.dump(vids, open(d / "videos.json", "w"))
    for name in ("mentions", "related", "authors"):
        json.dump({}, open(d / f"{name}.json", "w"))
    json.dump({"areas": [{"id": "software", "name": "Software",
                          "categories": [{"id": "web-development", "name": "Web development"}]}]}, open(d / "taxonomy.json", "w"))
    json.dump({"hn": {"label": "Hacker News", "short": "HN", "videos": 2}, "web": {"label": "The open web", "short": "Web", "videos": 2}},
              open(d / "communities.json", "w"))
    emb = np.eye(3, 4, dtype=np.float16)
    np.save(d / "embeddings.npy", emb)
    np.save(d / "title_emb.npy", emb)
    return str(d)


@pytest.fixture
def env():
    e = Environment(loader=FileSystemLoader(os.path.join(ROOT, "app", "templates")), autoescape=True)
    e.globals.update(SITE="HackerTube AI", BASE_URL="http://test", PRICE="$5", YEAR=2026, GA_ID="", POSTHOG_KEY="",
                     POSTHOG_HOST="", POSTHOG_PUBLIC="")
    return e


def base_ctx(**ctx):
    request = types.SimpleNamespace(url=types.SimpleNamespace(path="/"), query_params={})
    base = {"request": request, "user": None, "pro": False, "likes": [], "later": [], "hidden": [], "seen": [],
            "canonical": "http://test/", "title": "t", "description": ""}
    base.update(ctx)
    return base


def test_web_field_and_derived_fields_on_every_video(data_dir):
    cat = Catalog(data_dir)
    b, c = cat.by_vid["bbbbbbbbbbb"], cat.by_vid["ccccccccccc"]
    assert {k: b["web"][k] for k in ("domains", "months", "first_seen", "last_seen", "top_domains")} == {
        "domains": 30, "months": 4, "first_seen": "2026-03", "last_seen": "2026-10", "top_domains": ["dev.to", "lobste.rs"]}
    assert "web" not in cat.by_vid["aaaaaaaaaaa"]
    assert c["slug"] == "web-only-clip" and c["url"] == "/v/ccccccccccc/web-only-clip" and c["dur_s"] == "25:00"
    assert c["views_s"] == "10K" and c["year"] == "2020"
    assert cat.web_only == {"ccccccccccc"}


def test_the_open_web_is_a_community_with_its_count(data_dir):
    cat = Catalog(data_dir)
    web = cat.comms["web"]
    assert web["label"] == "The open web" and web["slug"] == "web" and web["short"] == "Web"
    assert sorted(web["vids"]) == ["bbbbbbbbbbb", "ccccccccccc"]
    assert sorted(cat.comms["hn"]["vids"]) == ["aaaaaaaaaaa", "bbbbbbbbbbb"]
    assert cat.comm_by_slug["web"] is web


def test_web_only_videos_stay_out_of_home_rows_by_default(data_dir):
    cat = Catalog(data_dir)
    assert "ccccccccccc" not in {v["vid"] for v in cat.ranked}
    assert "ccccccccccc" not in cat.essentials
    assert "ccccccccccc" not in {v["vid"] for v in cat.in_scope()}
    assert "ccccccccccc" not in {v["vid"] for v in cat.in_scope(areas=["software"])}
    rows, n = cat.feed_rows()
    assert n == 2 and all(v["vid"] != "ccccccccccc" for _, _, vids in rows for v in vids)
    assert {v["vid"] for v in cat.in_scope(communities=["hn"])} == {"aaaaaaaaaaa", "bbbbbbbbbbb"}


def test_filtering_by_the_open_web_includes_web_only_videos(data_dir):
    cat = Catalog(data_dir)
    assert {v["vid"] for v in cat.in_scope(communities=["web"])} == {"bbbbbbbbbbb", "ccccccccccc"}
    rows, n = cat.feed_rows(communities=["web"])
    assert n == 2


def test_the_switch_puts_web_only_videos_in_the_rows(data_dir, monkeypatch):
    cat = Catalog(data_dir, web_only_rows=True)
    assert "ccccccccccc" in {v["vid"] for v in cat.ranked}
    assert "ccccccccccc" in {v["vid"] for v in cat.in_scope()}
    assert cat.ranked[-1]["vid"] == "ccccccccccc", "a web-only video with one site ranks last"
    monkeypatch.setenv("HT_WEB_ONLY_ROWS", "1")
    import importlib
    import app.catalog as mod
    importlib.reload(mod)
    try:
        assert mod.Catalog(data_dir).web_only_rows is True
    finally:
        monkeypatch.delenv("HT_WEB_ONLY_ROWS")
        importlib.reload(mod)


def test_search_similar_and_personal_feed_still_work_with_web_only_videos(data_dir):
    cat = Catalog(data_dir)
    cat.embed = lambda texts: (_ for _ in ()).throw(RuntimeError("no model in tests"))
    hits = [v["vid"] for v in cat.search("web only")]
    assert hits and hits[0] == "ccccccccccc", "text match still finds a web-only video"
    assert len(cat.similar("bbbbbbbbbbb", 2)) == 2
    feed = cat.personal(cat.emb[cat.by_vid["ccccccccccc"]["i"]], n=3)
    assert [v["vid"] for v in feed][0] == "ccccccccccc"
    assert cat.interests(cat.emb[0]) == ["Web development"]
    assert cat.cats["web-development"]["vids"] == ["aaaaaaaaaaa", "bbbbbbbbbbb"]


def test_card_shows_a_sites_badge_only_when_web_exists(data_dir, env):
    cat = Catalog(data_dir)
    t = env.from_string('{% from "_macros.html" import card %}{{ card(v) }}')
    with_web = t.render(v=cat.by_vid["bbbbbbbbbbb"])
    assert "7 HN recs" in with_web and ">30 sites</span>" in with_web and "4 monthly crawls" in with_web
    plain = t.render(v=cat.by_vid["aaaaaaaaaaa"])
    assert "7 HN recs" in plain and "sites" not in plain
    web_only = t.render(v=cat.by_vid["ccccccccccc"])
    assert ">1 site</span>" in web_only and "recs" not in web_only and "still recommended" not in web_only


def test_video_page_line_and_sections(data_dir, env):
    cat = Catalog(data_dir)
    tpl = env.get_template("video.html")

    def render(vid):
        v = cat.by_vid[vid]
        return tpl.render(base_ctx(v=v, cats=[cat.cats[c] for c in v["cats"]], mentions=[], resume=0, resume_s="0:00",
                                   co=[], similar=cat.similar(vid, 2)))

    html = render("bbbbbbbbbbb")
    assert "Why Hacker News recommends it" in html
    assert "Why the open web links it" in html and "Course pages link it as the standard explanation." in html
    assert ("Linked by <strong>30</strong> independent sites across <strong>4</strong> monthly crawls of the open web, "
            "2026-03 to 2026-10, including dev.to, lobste.rs.") in html
    plain = render("aaaaaaaaaaa")
    assert "Why the open web links it" not in plain and "Linked by" not in plain and "What it is" in plain
    web_only = render("ccccccccccc")
    assert "Why Hacker News recommends it" not in web_only and "Recommended by" not in web_only
    assert "Linked by <strong>1</strong> independent site in the 2026-05 crawl of the open web." in web_only
    assert "What it is" not in web_only, "no summary, no empty section"


def test_communities_page_lists_the_open_web(data_dir, env):
    cat = Catalog(data_dir)
    comms = sorted(cat.comms.values(), key=lambda c: -len(c["vids"]))
    html = env.get_template("communities.html").render(base_ctx(comms=comms))
    assert 'href="/c/web">The open web <span>2</span>' in html
    assert "counts independent websites" in html
    without = env.get_template("communities.html").render(base_ctx(comms=[c for c in comms if c["id"] != "web"]))
    assert "open web" not in without


def test_curated_pool_defines_the_open_web_community_when_the_pack_marks_one(data_dir):
    vids = json.load(open(os.path.join(data_dir, "videos.json")))
    for v in vids:
        if v.get("web"):
            v["web"]["curated"] = v["vid"] == "ccccccccccc"   # only the web-only clip passed the rules
            v["web"]["reputable"] = 3
    json.dump(vids, open(os.path.join(data_dir, "videos.json"), "w"))
    cat = Catalog(data_dir)
    assert cat.web_curated is True
    assert cat.comms["web"]["vids"] == ["ccccccccccc"]            # the linked-everywhere HN talk is not listed
    assert "web" not in cat.sources_of(cat.by_vid["bbbbbbbbbbb"])  # and the home chip filter agrees
    assert cat.by_vid["bbbbbbbbbbb"]["web"]["domains"] == WEB_B["domains"]  # but it keeps its badge data


def test_list_page_shows_field_chips_with_the_active_one_marked(data_dir, env):
    cat = Catalog(data_dir)
    filters = [{"href": "/c/web", "label": "All fields", "n": 1200, "on": False},
               {"href": "/c/web?area=physics-space", "label": "Physics & space", "n": 692, "on": True}]
    html = env.get_template("list.html").render(base_ctx(heading="h", intro="i", filters=filters,
                                                         videos=[cat.by_vid["bbbbbbbbbbb"]], related=[], paragraphs=[]))
    assert 'class="chips field-chips"' in html
    assert '<a class="chip on" href="/c/web?area=physics-space" aria-current="page">Physics &amp; space <span>692</span></a>' in html
    assert '<a class="chip" href="/c/web">All fields <span>1,200</span></a>' in html
    plain = env.get_template("list.html").render(base_ctx(heading="h", intro="i", videos=[], related=[], paragraphs=[]))
    assert "field-chips" not in plain
