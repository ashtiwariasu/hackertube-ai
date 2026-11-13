"""End-to-end through the real app: verified watching, blind verdicts, ratings that need a watch, the Partner
Program (rate card, revenue share from referred Pro purchases, month close, balances, cash-out thresholds,
payments recorded by hand). In-memory store, a controlled clock, no network.
Loads the full catalogue and the embedding model, so it takes a few seconds."""
import html, os, re, sys, types

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.update({"DEV_LOGIN": "1", "STORE": "memory", "PILOT_TOPIC": "*", "ADMIN_UIDS": "admin", "HT_NOTES": "1",
                   "WATCH_SCALE": "1"})
os.environ.pop("DATA_BUCKET", None)

from fastapi.testclient import TestClient  # noqa: E402

from app import main as M  # noqa: E402
from app import notes as N  # noqa: E402


class Clock:
    t = 1_800_000_000.0

    def __call__(self):
        return self.t


def give_lengths():
    """The public demo catalogue has no durations (YouTube's metadata isn't redistributed), and the weekly 10 only
    takes videos of 6 to 90 minutes. There, give the best-ranked videos a length for these tests."""
    if not any(x["dur"] for x in M.cat.ranked[:50]):
        for x in M.cat.ranked[:200]:
            x["dur"] = 1200


def short_video(skip=()):
    """A ranked video of 3 to 5 minutes, so the watching stays quick. The public demo catalogue has no durations
    (YouTube's metadata isn't redistributed), so there the first ranked video is given one for the test."""
    v = next((x for x in M.cat.ranked if 180 <= x["dur"] <= 300 and x["vid"] not in skip), None)
    if v is None:
        v = next(x for x in M.cat.ranked if x["vid"] not in skip and not x["dur"])
        v["dur"] = 240
    return v


@pytest.fixture(scope="module")
def env():
    clock = Clock()
    mp = pytest.MonkeyPatch()
    mp.setattr(N, "clock", clock)
    mp.setattr(N, "moderate", lambda text, kind="note", title="": ("ok", ""))
    mp.setattr(N, "topic_fit", lambda videos, topic: None)   # no model calls in tests: picks stay unchecked
    mp.setattr(M, "HACKER_AUTO_APPROVE", False)
    give_lengths()
    v = short_video()
    yield clock, v
    mp.undo()


_n = [0]


def actor(uid=None):
    """A separate browser on its own network, signed in as uid if given."""
    _n[0] += 1
    c = TestClient(M.app, headers={"x-forwarded-for": f"10.{_n[0]}.0.1"})
    c.did = f"{_n[0]:032x}"
    if uid:
        c.get(f"/dev/login?uid={uid}", follow_redirects=False)
    return c


def watch(c, clock, v, fraction=0.7):
    d = c.post("/api/watch/beat", json={"vid": v["vid"]}).json()
    wt, pos = d["wt"], 0.0
    while pos < fraction * v["dur"]:
        clock.t += 30
        pos += 30
        d = c.post("/api/watch/beat", json={"vid": v["vid"], "wt": wt, "pos": pos, "played": 30}).json()
        wt = d["wt"]
    return wt, d


def state(c, v, wt=None):
    return c.post("/api/notes/state", json={"vid": v["vid"], "did": c.did, "wt": wt}).json()


GOOD = "A clear first look for people who know the basics but keep getting the details wrong."


def test_a_verdict_needs_a_real_watch_and_is_blind_until_given(env):
    clock, v = env
    a = actor()
    d = a.post("/api/watch/beat", json={"vid": v["vid"]}).json()
    assert d["ok"] and not d["real"]
    r = a.post("/api/verdict", json={"wt": d["wt"], "verdict": "rec", "text": GOOD, "did": a.did})
    assert r.status_code == 400
    wt, last = watch(a, clock, v)
    assert last["real"]
    assert a.post("/api/verdict", json={"wt": wt, "verdict": "rec", "text": "short", "did": a.did}).status_code == 400
    b = actor()
    assert state(b, v)["html"] == ""                               # blind: nothing before your own answer
    r = a.post("/api/verdict", json={"wt": wt, "verdict": "rec", "text": GOOD, "did": a.did}).json()
    assert r["ok"] and r["verdict"] == "rec" and r["note"] and GOOD in r["html"] and "A visitor" in r["html"]
    assert a.post("/api/verdict", json={"wt": wt, "verdict": "skip", "did": a.did}).status_code == 409
    assert state(b, v)["html"] == ""                               # still blind for someone who hasn't answered


def test_judging_needs_sign_in_and_a_real_watch_and_shows_totals_after(env):
    clock, v = env
    j = actor("judge-1")
    s = state(j, v)
    assert s["judge"] is None                                      # not watched yet: no note to judge
    note_id = next(n["id"] for n in M.nstore.notes.by("vid", v["vid"]))
    assert j.post("/api/judge", json={"note_id": note_id, "vote": "agree", "did": j.did}).status_code == 400
    wt, _ = watch(j, clock, v)
    s = state(j, v, wt)
    assert s["judge"] and s["judge"]["id"] == note_id and "A visitor" not in str(s["judge"])   # the writer stays hidden
    r = j.post("/api/judge", json={"note_id": note_id, "vote": "agree", "did": j.did, "wt": wt}).json()
    assert r["ok"] and r["tally"]["agree"] == 1 and r["tally"]["judged"] == 1
    assert j.post("/api/judge", json={"note_id": note_id, "vote": "agree", "did": j.did, "wt": wt}).status_code == 409
    anon = actor()
    assert anon.post("/api/judge", json={"note_id": note_id, "vote": "agree", "did": anon.did, "wt": wt}).status_code == 401


def week_video(i):
    """One of this week's 10 (made on first use, from the whole catalogue in these tests)."""
    return M.cat.get(M.this_week()["vids"][i])


def test_the_partner_program_from_joining_to_a_paid_spot_and_a_statement(env):
    clock, _ = env
    v = week_video(0)
    ada, lin, sam = actor("hk-ada"), actor("hk-lin"), actor("free-sam")
    assert sam.post("/api/review", json={"vid": v["vid"], "text": GOOD, "stance": "rec", "seen": True}).status_code == 403
    assert sam.post("/week/claim", data={"vid": v["vid"]}, follow_redirects=False).headers["location"] == "/earn"
    for c in (ada, lin):
        assert c.post("/hackers/apply", data={"why": "Docker"}, follow_redirects=False).status_code == 303
    assert M.store.get("hk-ada")["hacker"]["status"] == "applied"
    admin = actor("admin")
    assert admin.get("/hackers/admin").status_code == 200 and actor("nobody").get("/hackers/admin").status_code == 404
    for uid in ("hk-ada", "hk-lin"):
        admin.post("/hackers/admin/decide", data={"uid": uid, "decision": "approved"}, follow_redirects=False)
    assert M.hacker_status(M.store.get("hk-ada")) == "approved" and M.store.get("hk-ada")["handle"] == M.ids.handle("hk-ada")
    # Ada claims a paid spot; Lin doesn't, so her review takes one of the two unpaid spots
    r = ada.post("/week/claim", data={"vid": v["vid"], "next": v["url"]}, follow_redirects=False)
    assert r.headers["location"] == f"{v['url']}?msg=claimed#spot"
    assert "Your paid spot is held until" in ada.get(v["url"]).text
    assert ada.post("/week/claim", data={"vid": v["vid"]}, follow_redirects=False).headers["location"].endswith("msg=again")
    review = {"vid": v["vid"], "stance": "rec", "seen": True, "needs": "The basics", "moment": "1:10"}
    assert ada.post("/api/review", json=dict(review, text=GOOD, seen=False)).status_code == 400
    assert ada.post("/api/review", json=dict(review, text=GOOD, moment="soon")).status_code == 400
    assert state(ada, v)["paid_here"]
    r = ada.post("/api/review", json=dict(review, text="Worth it for engineers who want the reasoning behind the design, not just the steps.")).json()
    assert r["ok"] and r["hacker"]
    clock.t += 60
    r = lin.post("/api/review", json=dict(review, text="Skip it if you want a quick overview: the first third is slow, the payoff comes late.", stance="skip")).json()
    assert r["ok"]
    ada_id, lin_id = M.nts.doc_id(v["vid"], "hk-ada"), M.nts.doc_id(v["vid"], "hk-lin")
    a, b = M.nstore.notes.get(ada_id), M.nstore.notes.get(lin_id)
    assert (a["paid"], a["slot"], a["moment"]) == (True, "paid", 70) and (b["paid"], b["slot"]) == (False, "open")
    claim = M.nstore.claims.get(f"{M.this_week()['week']}_{v['vid']}_hk-ada")
    assert claim["status"] == "used" and claim["note_id"] == ada_id
    # partners never rate reviews, not even a competitor's
    wt, _ = watch(lin, clock, v, fraction=0.3)
    assert state(lin, v, wt)["judge"] is None and state(lin, v, wt)["no_rate"]
    r = lin.post("/api/judge", json={"note_id": ada_id, "vote": "disagree", "did": lin.did, "wt": wt})
    assert r.status_code == 403 and "Partners don't rate" in r.json()["error"]
    # ten viewers who watched: 4 of 5 agree with each review, Ada's panel fills first
    for k in range(10):
        c = actor(f"reader-{k}")
        wt, _ = watch(c, clock, v, fraction=0.3)
        target, vote = (ada_id, "agree" if k < 4 else "disagree") if k < 5 else (lin_id, "agree" if k < 9 else "disagree")
        assert c.post("/api/judge", json={"note_id": target, "vote": vote, "did": c.did, "wt": wt}).json()["ok"]
    out = M.all_assessed()
    assert out[ada_id]["status"] == out[lin_id]["status"] == "qualified" and out[ada_id]["first"]
    month = M.month_now()
    csv = admin.get(f"/hackers/statement.csv?month={month}").text
    totals = dict(re.findall(r"^\d{4}-\d{2},(hk-\w+),.*,(\d+\.\d\d),USD$", csv, re.M))
    assert totals == {"hk-ada": "10.00"}, csv                                  # a partner's first pays US$10; open spots don't pay
    page = ada.get("/earn").text
    assert "Your earnings" in page and "US$10.00" in page and "Partner" in page and "1 of 3 used" in page
    assert "Paid within 7 days" in page                                        # the first payment, from US$10
    assert "Paid partner" in ada.get(f"/r/{M.ids.handle('hk-ada')}").text
    assert "Monetisation: on." in ada.get("/me").text
    assert "Your paid review is posted" in ada.get("/week").text


def test_paid_spots_are_limited_by_video_by_level_and_by_the_monthly_cap(env, monkeypatch):
    clock, _ = env
    clock.t = M.nts.week_start(M.nts.week_shift(M.nts.week_key(clock.t), 1)) + 5 * 3600   # Monday 05:00 UTC, a fresh week
    monkeypatch.setattr(M, "HACKER_AUTO_APPROVE", True)
    p = {}
    for name in ("cap-a", "cap-b", "cap-c", "cap-d"):
        p[name] = actor(name)
        p[name].post("/hackers/apply", data={}, follow_redirects=False)

    def claim(name, v, back="/week"):
        loc = p[name].post("/week/claim", data={"vid": v["vid"], "next": back}, follow_redirects=False).headers["location"]
        return loc.split("msg=")[1]

    v1, v2, v3, v4 = (week_video(i) for i in (1, 2, 3, 4))
    assert [claim(n, v1) for n in ("cap-a", "cap-b", "cap-c")] == ["claimed"] * 3
    assert claim("cap-d", v1) == "full"                                        # 3 paid spots a video
    assert "Paid spots taken" in p["cap-d"].get("/week").text
    assert [claim("cap-a", v) for v in (v2, v3)] == ["claimed", "claimed"]
    assert claim("cap-a", v4) == "used"                                        # 3 a week at Partner level
    assert p["cap-b"].post("/week/release", data={"vid": v1["vid"]}, follow_redirects=False).headers["location"].endswith("released")
    assert claim("cap-d", v1) == "claimed"                                     # the spot went to someone else
    assert claim("cap-b", v1) == "again"                                       # and can't be taken back
    # a spot left unused for 48 hours frees up for others, and the review becomes an unpaid one
    clock.t += 49 * 3600
    assert claim("cap-b", v2) == "claimed"
    r = p["cap-a"].post("/api/review", json={"vid": v1["vid"], "stance": "rec", "seen": True,
                                             "text": "Good for backend engineers moving into ML: it skips the maths and shows the tradeoffs."}).json()
    assert r["ok"]
    assert M.nstore.notes.get(M.nts.doc_id(v1["vid"], "cap-a"))["slot"] == "open" and not M.nstore.notes.get(M.nts.doc_id(v1["vid"], "cap-a"))["paid"]
    # once the month's budget is committed, nobody can claim more
    monkeypatch.setattr(M.nts, "MONTH_CAP_CENTS", M.month_committed() + 400)
    assert claim("cap-c", v3) == "budget"
    assert M.spots_left(M.this_week()) == 0


def test_generic_or_self_copied_paid_reviews_never_earn_and_a_held_back_one_keeps_the_spot(env, monkeypatch):
    clock, _ = env
    monkeypatch.setattr(M, "HACKER_AUTO_APPROVE", True)
    g = actor("gen-a")
    g.post("/hackers/apply", data={}, follow_redirects=False)
    v6, v7, v8 = (week_video(i) for i in (6, 7, 8))
    for v in (v6, v7, v8):
        assert g.post("/week/claim", data={"vid": v["vid"]}, follow_redirects=False).headers["location"].endswith("claimed")
    real = M.nts.moderate
    monkeypatch.setattr(M.nts, "moderate", lambda text, kind="note", title="": ("hidden", "generic") if kind == "paid review" else real(text, kind, title))
    r = g.post("/api/review", json={"vid": v6["vid"], "stance": "rec", "seen": True, "text": "Great video, very informative and well explained for everyone."})
    assert r.status_code == 400 and "Your spot is still yours" in r.json()["error"]
    assert not M.nstore.notes.get(M.nts.doc_id(v6["vid"], "gen-a"))                     # not kept
    assert M.nstore.claims.get(f"{M.this_week()['week']}_{v6['vid']}_gen-a")["status"] == "held"
    monkeypatch.setattr(M.nts, "moderate", real)
    text = "For people who already write SQL daily: the second half on query plans is the part worth your evening."
    assert g.post("/api/review", json={"vid": v7["vid"], "stance": "rec", "seen": True, "text": text}).json()["ok"]
    assert g.post("/api/review", json={"vid": v8["vid"], "stance": "rec", "seen": True, "text": text}).json()["ok"]
    first, copy = (M.nstore.notes.get(M.nts.doc_id(v["vid"], "gen-a")) for v in (v7, v8))
    assert first["original"] and not copy["original"] and copy["own_sim"] > 0.95       # a template of their own earns nothing


def test_following_a_partners_link_never_shares_a_purchase(env, monkeypatch):
    made = {}

    class FakeSession:
        @staticmethod
        def create(**kw):
            made.update(kw)
            return types.SimpleNamespace(url="https://checkout.example/session")

    monkeypatch.setattr(M.stripe.checkout, "Session", FakeSession)
    buyer = actor("buyer-1")
    r = buyer.get(f"/r/{M.ids.handle('hk-ada')}")
    assert r.status_code == 200 and M.REF_COOKIE in r.cookies                 # remembered, only so they can't rate Ada
    assert buyer.post("/pro/checkout", follow_redirects=False).status_code == 303
    assert made["metadata"] == {"app": "hackertube", "uid": "buyer-1"}


def test_viewers_rate_after_a_few_minutes_but_not_reviews_by_the_partner_who_sent_them(env):
    clock, _ = env
    v = week_video(5)
    ada = actor("hk-ada")
    assert ada.post("/week/claim", data={"vid": v["vid"]}, follow_redirects=False).headers["location"].endswith("claimed")
    assert ada.post("/api/review", json={"vid": v["vid"], "stance": "rec", "seen": True,
                                         "text": "A good first look for people new to the idea, with a clear worked example."}).json()["ok"]
    assert 'data-row="waiting"' in actor().get("/").text                    # sent where ratings are needed
    nid = M.nts.doc_id(v["vid"], "hk-ada")
    friend = actor("friend-1")
    friend.get(f"{v['url']}?via={M.ids.handle('hk-ada')}")
    wt, d = watch(friend, clock, v, fraction=0.25)
    assert d["judged"] and not d["real"]                                      # a few minutes in: enough to rate
    assert state(friend, v, wt)["judge"] is None                              # but not Ada's review
    r = friend.post("/api/judge", json={"note_id": nid, "vote": "agree", "did": friend.did, "wt": wt})
    assert r.status_code == 400 and "partner's link" in r.json()["error"]
    stranger = actor("stranger-1")
    wt, _ = watch(stranger, clock, v, fraction=0.25)
    assert state(stranger, v, wt)["judge"]["id"] == nid
    assert stranger.post("/api/judge", json={"note_id": nid, "vote": "agree", "did": stranger.did, "wt": wt}).json()["ok"]


def test_the_first_payment_comes_within_a_week_and_months_close_frozen(env):
    clock, _ = env
    ada, lin, admin = actor("hk-ada"), actor("hk-lin"), actor("admin")
    assert ada.post("/earn/paypal", data={"paypal": "not an email"}, follow_redirects=False).headers["location"].endswith("paypal-invalid#payment")
    ada.post("/earn/paypal", data={"paypal": "Ada@Example.com"}, follow_redirects=False)
    assert M.store.get("hk-ada")["payout"]["paypal"] == "ada@example.com"
    r = lin.post("/earn/paypal", data={"paypal": "ada@example.com"}, follow_redirects=False)
    assert r.headers["location"].endswith("paypal-taken#payment") and not (M.store.get("hk-lin").get("payout") or {}).get("paypal")
    old = M.month_now()
    m = M.partner_money("hk-ada")
    assert (m["first"], m["live"], m["payable"], m["threshold"], m["due"]) == (True, 1000, 1000, 1000, True)   # this month counts
    page = admin.get("/hackers/admin").text
    assert "Payments due" in page and "ada@example.com" in page and "First payment: within 7 days" in page
    assert admin.post("/hackers/admin/paid", data={"uid": "hk-ada", "cents": 1001}, follow_redirects=False).headers["location"].endswith("over#balances")
    admin.post("/hackers/admin/paid", data={"uid": "hk-ada", "cents": 1000, "ref": "PP-123"}, follow_redirects=False)
    after = M.partner_money("hk-ada")
    assert (after["balance"], after["payable"], after["threshold"], after["first"]) == (0, 0, 10000, False)
    assert "Paid by PayPal to ada@example.com" in ada.get("/earn").text
    assert "warnings" in admin.get("/hackers/payouts.csv").text
    clock.t += 32 * 86400                                                     # into the next month
    M._closed["month"] = None
    m = M.partner_money("hk-ada")
    frozen = {s["month"]: s["total_cents"] for s in m["statements"]}
    assert frozen == {old: 1000} and m["balance"] == 0                         # the first payment came out of it
    first_review = next(n for n in M.nstore.notes.by("uid", "hk-ada") if n.get("paid") and n.get("slot") == "paid")
    M.nstore.notes.delete(first_review["id"])                                  # deleting a review later can't change a closed month
    assert M.month_rows(old)["hk-ada"]["total_cents"] == 1000
    assert M.nstore.pots.get(old)["status"] == "rolled"                       # nobody reached 20, so the pot grows
    assert M.pot_amount(M.month_now()) == 150_000 and "US$1,500" in actor().get("/earn").text


def test_the_diamond_pot_is_checked_by_hand_then_paid_like_any_earnings(env):
    ada, admin = actor("hk-ada"), actor("admin")
    M.nstore.pots.add("2026-12", {"month": "2026-12", "status": "review", "line": ["hk-lin", "hk-ada"], "cents": 100_000})
    page = admin.get("/hackers/admin").text
    assert "waiting for your check" in page and "Crown, add US$1,000.00" in page
    pot = lambda d: admin.post("/hackers/admin/pot", data=dict(d, month="2026-12"), follow_redirects=False)
    assert pot({"uid": "hk-ada", "decision": "steal"}).headers["location"].endswith("#pots")      # nothing happens
    actor("hk-ada").post("/hackers/admin/pot", data={"month": "2026-12", "uid": "hk-ada", "decision": "crown"}, follow_redirects=False)
    assert M.nstore.pots.get("2026-12")["status"] == "review"                                   # only an admin decides
    pot({"uid": "hk-lin", "decision": "pass"})
    assert M.nstore.pots.get("2026-12")["line"] == ["hk-ada"]
    pot({"uid": "hk-ada", "decision": "crown"})
    p = M.nstore.pots.get("2026-12")
    assert (p["status"], p["winner"], p["cents"]) == ("won", "hk-ada", 100_000)
    m = M.partner_money("hk-ada")
    assert m["payable"] == 100_000 and m["due"]                               # over the US$100 threshold
    earn = ada.get("/earn").text
    assert "Hall of Diamonds" in earn and "Diamond pot" in earn


def test_a_hacker_can_give_a_plain_verdict_and_their_verdict_notes_are_never_paid(env):
    clock, v = env
    ada = actor("hk-ada")
    wt, _ = watch(ada, clock, v)
    r = ada.post("/api/verdict", json={"wt": wt, "verdict": "rec", "did": ada.did}).json()
    assert r["ok"] and r["verdict"] == "rec" and not r["earn"]          # already a partner: no invitation
    tom = actor("free-tom")
    wt, _ = watch(tom, clock, v)
    r = tom.post("/api/verdict", json={"wt": wt, "verdict": "rec", "text": GOOD + " Tom.", "did": tom.did}).json()
    assert r["ok"] and r["note"] and r["earn"]                         # wrote a review: invited to get monetised


def test_signing_in_moves_a_visitors_verdict_and_note_to_the_account(env):
    clock, v2 = env
    v = short_video(skip={v2["vid"]})
    a = actor()
    wt, _ = watch(a, clock, v)
    assert a.post("/api/verdict", json={"wt": wt, "verdict": "rec", "text": GOOD, "did": a.did}).json()["ok"]
    a.get("/dev/login?uid=merged-user", follow_redirects=False)
    r = a.post("/api/notes/merge", json={"did": a.did}).json()
    assert r["ok"] and r["moved"] == 2
    n = M.nstore.notes.get(M.nts.doc_id(v["vid"], "merged-user"))
    assert n and n["uid"] == "merged-user" and n["handle"] == M.ids.handle("merged-user")
    page = a.get(f"/r/{n['handle']}")
    assert page.status_code == 200 and GOOD in page.text
    shared = actor().get(f"{v['url']}?via={n['handle']}").text
    assert "Shared by" in shared and GOOD in shared
    me = a.get("/me").text
    assert "Your reviews" in me and "Get monetised" in me
    a.post("/me/delete-notes", follow_redirects=False)
    assert not M.nstore.notes.by("uid", "merged-user") and not M.nstore.verdicts.by("uid", "merged-user")
    assert actor().get(f"/r/{n['handle']}").status_code == 404


def test_display_names_are_checked_and_never_the_google_name(env):
    c = actor("named-user")
    c.post("/me/name", data={"name": "<b>x</b>"}, follow_redirects=False)
    assert not (M.store.get("named-user") or {}).get("rec_name")
    c.post("/me/name", data={"name": "Ada L."}, follow_redirects=False)
    assert M.store.get("named-user")["rec_name"] == "Ada L."
    M._names.clear()
    assert M.display_name("named-user") == "Ada L." and M.display_name("free-sam").startswith("Reviewer r")


def test_the_earn_link_shows_only_while_a_paid_pilot_runs(env):
    c = actor()
    assert 'href="/earn" class="earn hide-sm"' in c.get("/about").text
    r = c.get("/hackers", follow_redirects=False)
    assert r.status_code == 301 and r.headers["location"] == "/earn"
    page = c.get("/earn").text
    assert "HackerTube Partner Program" in page and "Sign in to join" in page and "Why it can't be gamed" in page
    mp = pytest.MonkeyPatch()
    mp.setattr(M, "PILOT_TOPIC", "")
    try:
        assert 'href="/earn"' not in c.get("/about").text   # no pilot: the site never mentions earning
    finally:
        mp.undo()


# ---------------- submitting a video that isn't listed ----------------
def fake_item(vid, title="Building agents that use tools, explained step by step", privacy="public", dur="PT4M10S", channel="UCfake"):
    return {"id": vid, "status": {"privacyStatus": privacy, "embeddable": True},
            "contentDetails": {"duration": dur, "contentRating": {}},
            "snippet": {"title": title, "channelTitle": "Fake Channel", "channelId": channel,
                        "publishedAt": "2026-10-20T00:00:00Z", "liveBroadcastContent": "none"},
            "statistics": {"viewCount": "4321"}}


@pytest.fixture
def youtube(monkeypatch):
    items = {}
    monkeypatch.setattr(M, "yt_videos", lambda ids: {i: items[i] for i in ids if i in items})
    monkeypatch.setattr(M, "YT_KEY", "test-key")
    return items


REVIEW = "A clear first walk through tool use for engineers who know Python but have never built an agent."


def test_submitting_a_video_that_isnt_listed_from_link_to_vouched(env, youtube):
    clock, _ = env
    vid = "zzzzzzzzzz1"
    youtube[vid] = fake_item(vid, dur="PT12M")
    assert "Sign in to submit" in actor().get("/submit").text
    ada = actor("hk-ada")
    page = ada.get(f"/submit?url=https://youtu.be/{vid}").text
    assert "Building agents that use tools" in page and "you earn a US$10 finder fee" in page
    assert "Shorts can't be submitted" in html.unescape(ada.get("/submit?url=https://www.youtube.com/shorts/abcdefghijk").text)
    listed = M.cat.ranked[0]
    assert "It's already here" in ada.get(f"/submit?url={listed['vid']}").text
    youtube["zzzzzzzzzz2"] = fake_item("zzzzzzzzzz2", privacy="private")
    assert "Only public videos" in ada.get("/submit?url=zzzzzzzzzz2").text
    form = {"url": f"https://youtu.be/{vid}", "stance": "rec", "text": REVIEW, "needs": "Basic Python", "moment": "1:30"}
    assert "Only submit videos you have watched" in ada.post("/submit", data=form).text
    r = ada.post("/submit", data=dict(form, seen="1"), follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("?msg=submitted")
    doc = M.nstore.added.get(vid)
    assert doc["status"] == "new" and doc["uid"] == "hk-ada" and doc["dur"] == 720 and not doc["own"]
    note = M.nstore.notes.get(M.nts.doc_id(vid, "hk-ada"))
    assert not note["paid"] and note["review"] and note["submitted"] and note["moment"] == 90   # the finder fee comes later
    v = M.cat.get(vid)
    assert v and v["added"] and vid not in M.cat.by_vid                    # found, but not in rankings or search
    page = ada.get(r.headers["location"]).text
    assert "Added by" in page and "New find" in page and 'content="noindex' in page and "Thanks. Your video is added" in page
    assert 'data-row="new-finds"' in actor().get("/").text
    again = actor("hk-lin").post("/submit", data=dict(form, seen="1"), follow_redirects=False)
    assert again.headers["location"].endswith("?msg=listed")                 # someone else got there first
    for k in range(5):                                                       # five viewers watch and rate: 4 agree
        c = actor(f"sub-reader-{k}")
        wt, _ = watch(c, clock, v, fraction=0.3)
        s = state(c, v, wt)
        assert s["judge"] and s["judge"]["id"] == note["id"]
        assert c.post("/api/judge", json={"note_id": note["id"], "vote": "agree" if k < 4 else "disagree", "did": c.did, "wt": wt}).json()["ok"]
    assert M.nstore.added.get(vid)["status"] == "vouched" and M.cat.get(vid)["vouched"]
    page = actor().get(v["url"]).text
    assert 'content="noindex' not in page and "Vouched by viewers" in page
    # vouched: next week's 10 picks it first, and Ada, who found it, earns a finder fee that month
    assert (vid, "hk-ada") in [(x["vid"], u) for x, u in M.week_finds(set())]
    nxt = M.nts.week_shift(M.this_week()["week"], 1)
    w = M.make_week(nxt)
    assert w["vids"][0] == vid and w["found"] == {vid: "hk-ada"}
    row = M.nts.earnings({}, M.nts.ym(M.nts.week_start(nxt)), [w])["hk-ada"]
    assert (row["finds"], row["finder_cents"]) == (1, 1000)


def test_a_submission_by_someone_connected_to_the_creator_is_labelled_and_never_earns(env, youtube):
    vid = "zzzzzzzzzz3"
    youtube[vid] = fake_item(vid, title="My own course on fine-tuning language models")
    lin = actor("hk-lin")
    r = lin.post("/submit", data={"url": vid, "stance": "rec", "text": REVIEW, "seen": "1", "own": "1"}, follow_redirects=False)
    assert r.status_code == 303
    note = M.nstore.notes.get(M.nts.doc_id(vid, "hk-lin"))
    assert note["own"] and not note["paid"] and M.nstore.added.get(vid)["own"]
    assert "Connected to the creator" in state(lin, M.cat.get(vid))["html"]
    M.store.upsert("hk-lin", {"yt_channel_id": "UCmine"})                  # their own channel, known from connecting YouTube
    youtube["zzzzzzzzzz4"] = fake_item("zzzzzzzzzz4", channel="UCmine")
    lin.post("/submit", data={"url": "zzzzzzzzzz4", "stance": "rec", "text": REVIEW, "seen": "1"}, follow_redirects=False)
    assert not M.nstore.notes.get(M.nts.doc_id("zzzzzzzzzz4", "hk-lin"))["paid"]


def test_submitted_videos_are_re_read_from_youtube_and_dropped_when_no_longer_public(env, youtube):
    clock, _ = env
    vid = "zzzzzzzzzz5"
    youtube[vid] = fake_item(vid)
    actor("free-sam").post("/submit", data={"url": vid, "stance": "skip", "text": REVIEW, "seen": "1"}, follow_redirects=False)
    assert M.cat.get(vid) and not M.nstore.notes.get(M.nts.doc_id(vid, "free-sam"))["paid"]   # not a partner: unpaid
    youtube[vid] = fake_item(vid, title="Renamed by its owner")
    clock.t += 8 * 86400
    M.sync_added()
    assert M.cat.get(vid)["title"] == "Renamed by its owner"
    youtube[vid] = fake_item(vid, privacy="private")
    clock.t += 8 * 86400
    M.sync_added()
    assert M.nstore.added.get(vid)["status"] == "gone" and M.cat.get(vid) is None


def test_a_spot_claimed_late_on_sunday_keeps_its_48_hours_into_the_new_week(env, monkeypatch):
    clock, _ = env
    monkeypatch.setattr(M, "HACKER_AUTO_APPROVE", True)
    nxt = M.nts.week_shift(M.nts.week_key(clock.t), 1)
    clock.t = M.nts.week_start(M.nts.week_shift(nxt, 1)) - 120          # Sunday 23:58 UTC, the end of week `nxt`
    late = actor("late-lu")
    late.post("/hackers/apply", data={"why": "LLMs"}, follow_redirects=False)
    v = week_video(0)
    old = M.this_week()["week"]
    assert late.post("/week/claim", data={"vid": v["vid"], "next": "/week"}, follow_redirects=False).headers["location"].endswith("msg=claimed")
    clock.t += 10 * 60                                                        # Monday 00:08: a new weekly 10
    assert M.this_week()["week"] != old and v["vid"] not in M.this_week()["vids"]
    page = late.get(v["url"]).text
    assert "From last week" in page and "Your paid spot is held until" in page
    assert "Your spot from last week" in late.get("/week").text
    assert "from last week" in late.get("/earn").text
    assert state(late, v)["paid_here"]
    r = late.post("/api/review", json={"vid": v["vid"], "stance": "rec", "seen": True, "needs": "The basics",
                                       "text": "For people who use chat models daily and want to know what attention actually does."}).json()
    assert r["ok"]
    n = M.nstore.notes.get(M.nts.doc_id(v["vid"], "late-lu"))
    assert (n["paid"], n["slot"], n["week"]) == (True, "paid", old)
    assert M.nstore.claims.get(f"{old}_{v['vid']}_late-lu")["status"] == "used"
    # once its 48 hours are up, a spot from last week is gone
    clock.t += 49 * 3600
    assert M.live_claim("late-lu", v["vid"])[1] is None
