"""Vouched submissions as a community pack for the weekly build, and how the app labels that community."""
import json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import catalog as C  # noqa: E402
from app import notes as N  # noqa: E402
from app import refresh as R  # noqa: E402
from app import viewers_pack as VP  # noqa: E402

TEXT = "A clear first look at tool calling for engineers who have used an LLM API but never built an agent."


def note(nid, vid, uid, ts, stance=N.REC, **kw):
    return {"id": nid, "vid": vid, "uid": uid, "who": uid, "ts": ts, "stance": stance, "text": TEXT, "mod": "ok",
            "did_h": f"d-{nid}", "net_h": f"n-{nid}", "original": True, "paid": True, **kw}


def panel(nid, vid, start, agree):
    return [{"id": f"{nid}_j{i}", "note_id": nid, "vid": vid, "judge": f"j{nid}{i}", "judge_who": f"j{nid}{i}",
             "vote": N.AGREE if i < agree else N.DISAGREE, "ts": start + i, "did_h": f"dj{nid}{i}", "net_h": f"nj{nid}{i}"}
            for i in range(5)]


def added(vid, status="vouched", ts=1_800_000_000):
    return {"vid": vid, "status": status, "ts": ts, "title": "Building LLM Agents with Tool Use", "channel": "Jay Alammar",
            "topic": "llms-generative-ai", "uid": "u1"}


def test_a_vouched_submission_becomes_one_pack_row_without_review_text_or_names():
    t = 1_800_000_000
    notes = [note("a", "vidAAAAAAAA", "u1", t), note("b", "vidAAAAAAAA", "u2", t + 5, stance=N.SKIP)]
    verdicts = [{"vid": "vidAAAAAAAA", "who": "u3", "uid": "u3", "verdict": N.REC, "ts": t + 50}]
    rows = VP.pack_rows([added("vidAAAAAAAA"), added("vidBBBBBBBB", status="new")], notes, verdicts,
                        panel("a", "vidAAAAAAAA", t + 100, 4))
    assert len(rows) == 1
    r = rows[0]
    assert (r["vid"], r["community"], r["categories"]) == ("vidAAAAAAAA", "viewers", ["llms-generative-ai"])
    assert r["stats"]["recommenders"] == 2 and r["stats"]["mentions"] == 2 and r["score"] == 2 + 4
    assert "4 of the first 5" in r["why"] and TEXT not in json.dumps(r) and "u1" not in json.dumps(r)


def test_a_submission_whose_counted_review_was_deleted_drops_out():
    assert VP.pack_rows([added("vidAAAAAAAA")], [], [], []) == []


def test_the_pack_file_is_always_written_so_last_weeks_rows_clear(tmp_path):
    VP.write(str(tmp_path / "viewers"), [{"vid": "vidAAAAAAAA"}])
    VP.write(str(tmp_path / "viewers"), [])
    assert (tmp_path / "viewers" / "videos.jsonl").read_text() == ""


def test_a_failure_reading_the_store_never_stops_the_refresh(monkeypatch):
    def boom(db):
        raise RuntimeError("store down")
    monkeypatch.setattr(VP, "from_firestore", boom)
    monkeypatch.setitem(sys.modules, "google.cloud.firestore", type(sys)("fake_firestore"))
    sys.modules["google.cloud.firestore"].Client = lambda project=None: None
    R.write_viewers_pack()                                                  # logs and carries on


def test_the_app_labels_the_viewers_community():
    v = {"comm": {"viewers": {"recommenders": 3}}, "primary": "viewers", "badge": "3 viewers recs",
         "why_list": [{"c": "viewers", "label": "viewers"}]}
    C.fix_viewers(v)
    assert v["badge"] == "3 viewer recs" and v["why_list"][0]["label"] == "HackerTube viewers"
