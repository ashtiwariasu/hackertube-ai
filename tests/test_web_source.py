"""The open-web pack: row validation, loading, scoring and the merge step used by scripts/build_catalog.py.

Self-contained: no conftest fixtures, no raw dataset, no model."""
import json, os, sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app.web_source import (WEB_COMM, WEB_FIELDS, apply_web, derive_web_only, load_web_pack, web_topic_cats,  # noqa: E402
                            parse_web_row, web_only, web_score)

GOOD = {"vid": "8aGhZQkoFbQ", "domains": 12, "months": 4, "first_seen": "2026-01", "last_seen": "2026-10",
        "top_domains": ["Example.org ", "blog.example.com"], "why": "Everyone links it."}


def test_parse_row_keeps_the_five_fields_and_why():
    rec = parse_web_row(GOOD)
    assert set(WEB_FIELDS) <= set(rec)
    assert rec["domains"] == 12 and rec["months"] == 4
    assert rec["first_seen"] == "2026-01" and rec["last_seen"] == "2026-10"
    assert rec["top_domains"] == ["example.org", "blog.example.com"]
    assert rec["why"] == "Everyone links it."
    assert rec["reputable"] == 0 and rec["curated"] is False   # defaults when the pack does not say


def test_parse_row_keeps_reputable_count_and_curated_flag():
    rec = parse_web_row({**GOOD, "reputable": 7, "curated": True})
    assert rec["reputable"] == 7 and rec["curated"] is True
    with pytest.raises(ValueError):
        parse_web_row({**GOOD, "reputable": -1})


def test_parse_row_caps_top_domains_at_five_and_drops_empty_why():
    rec = parse_web_row({**GOOD, "top_domains": [f"d{i}.com" for i in range(9)], "why": "  "})
    assert len(rec["top_domains"]) == 5
    assert "why" not in rec


@pytest.mark.parametrize("bad", [
    {**GOOD, "vid": "short"},
    {**GOOD, "domains": 0},
    {**GOOD, "domains": "12"},
    {**GOOD, "domains": True},
    {**GOOD, "months": -1},
    {**GOOD, "first_seen": "2026-13"},
    {**GOOD, "last_seen": "Jan 2026"},
    {**GOOD, "first_seen": "2026-11", "last_seen": "2026-02"},
    {**GOOD, "top_domains": "example.org"},
    {**GOOD, "top_domains": ["ok.org", ""]},
])
def test_parse_row_rejects_bad_rows(bad):
    with pytest.raises(ValueError):
        parse_web_row(bad)


def test_load_web_pack_reads_a_folder_dedupes_and_tolerates_blank_lines(tmp_path):
    pack = tmp_path / "web"
    pack.mkdir()
    rows = [GOOD, {}, {**GOOD, "domains": 20, "months": 2}, {**GOOD, "vid": "aircAruvnKk", "domains": 3, "months": 3}]
    (pack / "videos.jsonl").write_text("\n".join(json.dumps(r) if r else "" for r in rows) + "\n", encoding="utf-8")
    web = load_web_pack(str(pack))
    assert set(web) == {"8aGhZQkoFbQ", "aircAruvnKk"}
    assert web["8aGhZQkoFbQ"]["domains"] == 20, "the row with more domains wins"
    assert load_web_pack(str(pack / "videos.jsonl")) == web, "a file path works too"


def test_load_web_pack_missing_is_empty_and_bad_row_names_the_line(tmp_path):
    assert load_web_pack(str(tmp_path / "nope")) == {}
    f = tmp_path / "videos.jsonl"
    f.write_text(json.dumps(GOOD) + "\n" + json.dumps({**GOOD, "months": 0}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match=":2:"):
        load_web_pack(str(f))
    f.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not JSON"):
        load_web_pack(str(f))


def test_web_score_is_a_small_bonus_with_other_sources_and_bounded_alone():
    small = {"domains": 3, "months": 2}
    huge = {"domains": 100_000, "months": 60}
    assert 0 < web_score(small, True) < web_score(huge, True) <= 1.5
    assert web_score(small, False) == web_score(small, True) + 1.0
    assert web_score(huge, False) <= 4.5


def test_apply_web_attaches_and_adds_only_usable_videos():
    videos = {"8aGhZQkoFbQ": {"vid": "8aGhZQkoFbQ", "comm": {"hn": {"score": 5}}}}
    web = {"8aGhZQkoFbQ": parse_web_row(GOOD),
           "aircAruvnKk": parse_web_row({**GOOD, "vid": "aircAruvnKk", "curated": True}),
           "zzzzzzzzzzz": parse_web_row({**GOOD, "vid": "zzzzzzzzzzz", "curated": True})}

    def make(vid):
        return None if vid == "zzzzzzzzzzz" else {"vid": vid, "title": "t", "comm": {}}

    assert apply_web(videos, web, make) == 1
    assert set(videos) == {"8aGhZQkoFbQ", "aircAruvnKk"}
    assert videos["8aGhZQkoFbQ"]["web"]["domains"] == 12 and videos["8aGhZQkoFbQ"]["comm"]["hn"]["score"] == 5
    assert web_only(videos["aircAruvnKk"]) and not web_only(videos["8aGhZQkoFbQ"])


def test_apply_web_never_admits_an_unjudged_video_on_links_alone():
    videos = {"8aGhZQkoFbQ": {"vid": "8aGhZQkoFbQ", "comm": {"hn": {"score": 5}}}}
    web = {"8aGhZQkoFbQ": parse_web_row({**GOOD, "curated": False}),   # a community video keeps its web record
           "aircAruvnKk": parse_web_row({**GOOD, "vid": "aircAruvnKk", "curated": False})}
    assert apply_web(videos, web, lambda vid: {"vid": vid, "title": "t", "comm": {}}) == 0
    assert set(videos) == {"8aGhZQkoFbQ"} and videos["8aGhZQkoFbQ"]["web"]["curated"] is False


def test_derive_web_only_gives_the_shape_templates_and_feeds_expect():
    v = {"vid": "aircAruvnKk", "comm": {}, "web": parse_web_row({**GOOD, "vid": "aircAruvnKk"})}
    derive_web_only(v)
    assert v["primary"] == WEB_COMM
    assert v["score"] == web_score(v["web"], False)
    assert v["recs"] == 0 and v["badge"] == "" and v["why_list"] == []
    assert v["why_hn"] == "Everyone links it."
    assert v["hn"] == {"recommenders": 0, "rec_months": 0, "first_year": None, "last_year": None}


def test_sample_pack_has_20_catalogue_videos():
    web = load_web_pack(os.path.join(ROOT, "packs", "web-sample"))
    assert len(web) == 20
    catalogue = os.path.join(ROOT, "app", "data", "videos.json")
    if os.path.exists(catalogue):
        known = {v["vid"] for v in json.load(open(catalogue, encoding="utf-8"))}
        assert set(web) <= known


def test_parse_row_keeps_the_gates_kind_and_area_lowercased():
    out = parse_web_row({"vid": "0fKBhvDjuy0", "domains": 5, "months": 1, "first_seen": "2026-10", "last_seen": "2026-10",
                         "top_domains": ["a.org"], "kind": "Documentary", "area": "physics-space"})
    assert out["kind"] == "documentary" and out["area"] == "physics-space"
    bare = parse_web_row({"vid": "0fKBhvDjuy0", "domains": 5, "months": 1, "first_seen": "2026-10", "last_seen": "2026-10",
                          "top_domains": [], "kind": "", "area": 7})
    assert "kind" not in bare and "area" not in bare


def test_parse_row_keeps_up_to_two_topics():
    out = parse_web_row({"vid": "0fKBhvDjuy0", "domains": 5, "months": 1, "first_seen": "2026-10", "last_seen": "2026-10",
                         "top_domains": [], "topics": ["Cosmology-Astronomy", "physics-pop", "third"]})
    assert out["topics"] == ["cosmology-astronomy", "physics-pop"]
    assert "topics" not in parse_web_row({"vid": "0fKBhvDjuy0", "domains": 5, "months": 1, "first_seen": "2026-10",
                                          "last_seen": "2026-10", "top_domains": [], "topics": "nope"})


def test_web_topic_cats_fills_only_empty_categories_with_known_topics():
    v = {"cats": [], "web": {"topics": ["unknown", "mechanics"]}}
    web_topic_cats(v, {"mechanics", "optics"})
    assert v["cats"] == ["mechanics"]
    kept = {"cats": ["web-development"], "web": {"topics": ["mechanics"]}}
    web_topic_cats(kept, {"mechanics"})
    assert kept["cats"] == ["web-development"]


def test_web_topic_cats_takes_only_the_first_known_topic():
    v = {"cats": [], "web": {"topics": ["optics", "mechanics"]}}
    web_topic_cats(v, {"mechanics", "optics"})
    assert v["cats"] == ["optics"]
