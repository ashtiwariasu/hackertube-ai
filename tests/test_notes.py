"""Verdicts, notes and reader judgments: watch tokens, the panel rule (one voice per network and browser),
early-find credit, the Partner Program's phase 1 (pay, levels, the weekly 10, paid spots, the monthly cap, the
Diamond pot, signs of a farm) and moderation, all without the app, the catalogue or the network."""
import os, sys, types

import pytest
from itsdangerous import BadSignature

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import notes as N  # noqa: E402


class Clock:
    def __init__(self, t=1_800_000_000):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(N, "clock", c)
    return c


# ---------------- watch tokens ----------------
def play(w, clock, tok, seconds, step=30, speed=1.0, start_pos=0.0):
    pos = start_pos
    st = None
    for _ in range(int(seconds // step)):
        clock.t += step
        pos += step * speed
        tok, st = w.beat(tok, pos, step)
    return tok, st, pos


def test_a_straight_watch_becomes_real(clock):
    w = N.Watch("s")
    tok, st = w.start("vid00000001")
    assert st["s"] == 0 and st["m"] == 0
    tok, st, _ = play(w, clock, tok, 360)            # 6 minutes of a 10-minute video
    assert N.real(st, 600) and N.judged(st, 600)
    tok2, st2 = w.start("vid00000001")
    assert not N.real(st2, 600)                       # a fresh view starts from nothing


def test_seeking_to_the_end_does_not_count(clock):
    w = N.Watch("s")
    tok, _ = w.start("vid00000001")
    clock.t += 5
    tok, st = w.beat(tok, 3000, 5)
    assert st["m"] <= 5 * N.SPEED_MAX + 10
    assert not N.real(st, 3200)


def test_claimed_play_cannot_exceed_the_clock(clock):
    w = N.Watch("s")
    tok, _ = w.start("vid00000001")
    clock.t += 10
    tok, st = w.beat(tok, 10, 600)
    assert st["s"] <= 13
    clock.t += 1000                                   # a long pause, then a huge claim
    tok, st = w.beat(tok, 20, 5000)
    assert st["s"] <= 13 + N.BEAT_CREDIT_MAX


def test_double_speed_still_counts(clock):
    w = N.Watch("s")
    tok, _ = w.start("vid00000001")
    tok, st, _ = play(w, clock, tok, 360, speed=2.0)  # 12 minutes of a 20-minute video in 6
    assert N.real(st, 1200)


def test_a_forged_or_edited_token_is_refused(clock):
    w = N.Watch("s")
    tok, _ = w.start("vid00000001")
    with pytest.raises(BadSignature):
        w.beat(tok[:-2] + "xx", 10, 10)
    with pytest.raises(BadSignature):
        N.Watch("other-secret").beat(tok, 10, 10)


def test_a_receipt_carries_progress_to_the_next_view_of_the_same_video_only(clock):
    w = N.Watch("s")
    tok, _ = w.start("vid00000001")
    tok, st, _ = play(w, clock, tok, 180)
    _, again = w.start("vid00000001", receipt=tok)
    assert again["s"] == st["s"] and again["m"] == st["m"]
    _, other = w.start("vid00000002", receipt=tok)
    assert other["s"] == 0
    _, junk = w.start("vid00000001", receipt="not-a-token")
    assert junk["s"] == 0


def test_skip_needs_less_than_a_recommendation(clock):
    w = N.Watch("s")
    tok, _ = w.start("vid00000001")
    tok, st, _ = play(w, clock, tok, 120)
    assert N.judged(st, 600) and not N.real(st, 600)


# ---------------- ids and text ----------------
def test_ids_are_stable_one_way_and_group_a_network():
    ids = N.Ids("secret")
    h = ids.handle("1234567890")
    assert N.HANDLE_RE.match(h) and h == ids.handle("1234567890") and h != N.Ids("other").handle("1234567890")
    assert ids.network("203.0.113.7") == ids.network("203.0.113.200") != ids.network("203.0.114.7")
    assert ids.network("2001:db8:1:2:aaaa::1") == ids.network("2001:db8:1:2:bbbb::9")
    assert ids.device("abc") == ids.device("abc") != ids.device("abd")


def test_note_and_name_checks():
    assert N.check_note("too short")
    assert N.check_note("Great for people new to Docker, see https://x.io for more") != ""
    assert N.check_note("Write to me at someone@example.com about this lecture") != ""
    assert N.check_note("For anyone who knows containers but keeps tripping over localhost networking.") == ""
    assert N.check_name("A") and N.check_name("x" * 31) and N.check_name("<script>")
    assert N.check_name("Ada L.") == ""
    assert N.clean_text("  a\n\tb\x00c  ") == "a b c"


# ---------------- assessing notes ----------------
def note(nid, vid, uid, ts, stance=N.REC, **kw):
    kw.setdefault("paid", bool(uid))
    return {"id": nid, "vid": vid, "uid": uid, "who": uid or kw.pop("who", "danon"), "ts": ts, "stance": stance,
            "mod": "ok", "did_h": f"d-{nid}", "net_h": f"n-{nid}", "original": True, **kw}


def judgment(nid, judge, ts, vote=N.AGREE, **kw):
    return {"id": f"{nid}_{judge}", "note_id": nid, "judge": judge, "judge_who": judge, "vote": vote, "ts": ts,
            "did_h": f"d-{judge}", "net_h": f"n-{judge}", **kw}


def panel(nid, start, votes):
    return [judgment(nid, f"j{nid}{i}", start + i, v) for i, v in enumerate(votes)]


def test_a_review_that_counts_pays_5_dollars_and_a_partners_very_first_pays_10():
    ns = [note("a", "v1", "u1", 100), note("b", "v2", "u1", 150), note("c", "v1", "u2", 200)]
    js = panel("a", 300, [N.AGREE] * 4 + [N.DISAGREE]) + panel("b", 350, [N.AGREE] * 5) + panel("c", 400, [N.AGREE] * 5)
    out = N.assess(ns, js)
    assert {k: a["cents"] for k, a in out.items()} == {"a": 500, "b": 500, "c": 500}
    assert N.first_ever(out) == {"u1": "a", "u2": "c"}
    rows = N.earnings(out, N.ym(404))
    assert {u: r["total_cents"] for u, r in rows.items()} == {"u1": 1500, "u2": 1000}   # 10 + 5, and 10
    assert (rows["u1"]["first_ever"], rows["u1"]["counted"], rows["u1"]["review_cents"]) == (1, 2, 1500)


def test_the_first_to_spot_it_badge_stays_but_pays_nothing_extra():
    ns = [note("old", "v1", "u1", 100), note("new", "v1", "u2", 200)]
    js = panel("new", 300, [N.AGREE] * 5)
    out = N.assess(ns, js + panel("old", 400, [N.AGREE] * 5))
    assert out["new"]["first"] and not out["old"]["first"] and out["new"]["cents"] == out["old"]["cents"] == 500


def test_levels_give_more_spots_not_more_pay_and_need_quality():
    ns = [note(f"n{i}", f"v{i}", "u1", 100 + i) for i in range(7)]
    js = []
    for i in range(5):
        js += panel(f"n{i}", 1000 + i * 10, [N.AGREE] * 5)
    js += panel("n5", 1100, [N.DISAGREE] * 5)        # 5 of 6 rated reviews counted: over 80%
    mine = list(N.assess(ns, js).values())
    lv = N.level_for(mine, 2000)
    assert (lv["key"], lv["spots"], lv["early"], lv["counted30"], lv["rated30"]) == ("silver", 6, 2, 5, 6)
    assert lv["next"]["name"] == "Gold" and lv["next"]["more"] == 15
    r = N.earnings(N.assess(ns, js), N.ym(1100))["u1"]
    assert r["review_cents"] == 1000 + 4 * 500        # the same rate at every level
    js += panel("n6", 1200, [N.DISAGREE] * 5)        # 5 of 7: under 80%, so back to Partner
    lv = N.level_for(list(N.assess(ns, js).values()), 2000)
    assert (lv["key"], lv["spots"], lv["rate_ok"]) == ("partner", 3, False)
    assert N.level_for(mine, 1100 + 31 * 86400)["key"] == "partner"   # only the last 30 days count


def test_finder_fees_come_from_the_weekly_10_that_starts_that_month():
    weeks = [{"week": "2026-11-09", "found": {"v1": "u1", "v2": ""}}, {"week": "2026-12-07", "found": {"v3": "u1"}}]
    rows = N.earnings({}, "2026-11", weeks)
    assert list(rows) == ["u1"] and (rows["u1"]["finds"], rows["u1"]["finder_cents"], rows["u1"]["total_cents"]) == (1, 1000, 1000)
    assert N.earnings({}, "2026-11", weeks, exclude={"u1"}) == {}       # staff never earn


def test_first_payment_from_10_dollars_then_100_paid_by_the_7th():
    assert N.threshold([]) == 1000 and N.threshold([{"cents": 1000}]) == 10000
    assert N.pay_by("2026-11") == "7 December 2026" and N.pay_by("2026-12") == "7 January 2027"
    assert N.money(123456) == "US$1,234.56"
    assert N.month_before("2027-01") == "2026-12" and N.month_after("2026-12") == "2027-01"
    assert N.median([]) == 0 and N.median([0, 0, 500]) == 0 and N.median([0, 500, 1000, 1500]) == 750


def test_a_room_full_of_accounts_counts_once_on_a_panel():
    ns = [note("a", "v1", "u1", 100)]
    farm = [judgment("a", f"f{i}", 200 + i, net_h="n-farm") for i in range(5)]            # five accounts, one network
    same_browser = [judgment("a", f"b{i}", 300 + i, did_h="d-shared") for i in range(3)]   # three accounts, one browser
    a = N.assess(ns, farm + same_browser)["a"]
    assert (a["judged"], a["status"]) == (2, "waiting")                                     # one of each counts
    real = panel("a", 400, [N.AGREE] * 3)
    a = N.assess(ns, farm + same_browser + real)["a"]
    assert a["judged"] == 5 and len({j["net_h"] for j in a["panel"]}) == 5


# ---------------- the weekly 10, paid spots and the pot ----------------
def vid(i, channel=None, dur=1200):
    return {"vid": f"v{i:02d}", "channel": channel or f"c{i}", "dur": dur}


def test_weeks_start_on_monday_utc():
    t = N.week_start("2026-11-09")
    assert N.week_key(t) == N.week_key(t + 6 * 86400 + 86399) == "2026-11-09"
    assert N.week_key(t - 1) == "2026-11-02" and N.week_shift("2026-11-09", 1) == "2026-11-16"
    assert N.week_shift("2026-11-02", -1) == "2026-10-26"
    gold, partner = N.LEVELS[2], N.LEVELS[0]
    assert N.opens_at("2026-11-09", gold) == t and N.opens_at("2026-11-09", partner) == t + 4 * 3600


def test_the_weekly_10_is_the_same_everywhere_one_per_channel_and_never_recent():
    pool = [vid(i) for i in range(60)] + [vid(60, channel="c1"), vid(61, dur=200), vid(62, dur=99999)]
    finds = [(vid(70), "u1"), (vid(71), ""), (vid(72), "u2"), (vid(73), "u3")]
    a, found = N.pick_week("2026-11-09", pool, finds, recent={"v05"}, size=10)
    assert a == N.pick_week("2026-11-09", pool, finds, recent={"v05"}, size=10)[0]       # every server picks the same
    assert a != N.pick_week("2026-11-16", pool, finds, recent={"v05"}, size=10)[0]       # a new week, a new mix
    assert len(a) == 10 and len(set(a)) == 10 and "v05" not in a and "v61" not in a and "v62" not in a
    assert a[:3] == ["v70", "v71", "v72"] and found == {"v70": "u1", "v71": "", "v72": "u2"}   # at most 3 finds
    chans = [next(v["channel"] for v in pool + [f for f, _ in finds] if v["vid"] == x) for x in a]
    assert len(set(chans)) == len(chans)                                                  # one per channel


def test_the_weekly_10_grows_only_when_reviews_get_rated_in_time():
    assert N.next_size(None, 0, 0) == 10
    assert N.next_size(10, 8, 10) == 15 and N.next_size(15, 9, 10) == 15                 # half again, up to 15
    assert N.next_size(10, 4, 10) == 7 and N.next_size(5, 0, 10) == 5                    # fewer, down to 5
    assert N.next_size(10, 0, 2) == 10                                                   # too few to judge by


def claim(uid, vid_, status="held", ts=0, until=10**12):
    return {"uid": uid, "vid": vid_, "status": status, "ts": ts, "until": until}


def test_claims_open_by_level_and_spots_cant_be_hoarded():
    key, partner, gold = "2026-11-09", N.LEVELS[0], N.LEVELS[2]
    t = N.opens_at(key, partner)
    ok = dict(featured=True, reviewed=False, own=False, mine_week=[], video_claims=[], committed=0, cost=500)
    assert N.claim_problem(partner, key, t, **ok) == ""
    assert N.claim_problem(partner, key, t - 60, **ok) == "early" and N.claim_problem(gold, key, t - 60, **ok) == ""
    assert N.claim_problem(partner, key, t, **dict(ok, featured=False)) == "notweek"
    assert N.claim_problem(partner, key, t, **dict(ok, reviewed=True)) == "reviewed"
    assert N.claim_problem(partner, key, t, **dict(ok, own=True)) == "own"
    spent = [claim("u1", "a", "used"), claim("u1", "b", "released"), claim("u1", "c", "held", until=1)]   # expired still counts
    assert N.claim_problem(partner, key, t, **dict(ok, mine_week=spent)) == "used"
    assert N.claim_problem(partner, key, t, **dict(ok, mine_week=spent[:2] + [claim("u1", "c", "lost")])) == ""
    taken = [claim(f"u{i}", "x") for i in range(3)]
    assert N.claim_problem(partner, key, t, **dict(ok, video_claims=taken)) == "full"
    assert N.claim_problem(partner, key, t, **dict(ok, video_claims=taken[:2] + [claim("u9", "x", until=1)])) == ""
    assert N.claim_problem(partner, key, t, **dict(ok, committed=N.MONTH_CAP_CENTS - 400)) == "budget"
    assert "Silver" in N.claim_message("early", N.LEVELS[1], key) and "6 paid spots" in N.claim_message("used", N.LEVELS[1], key)


def test_the_monthly_cap_counts_everything_that_could_still_be_paid():
    ns = [note("a", "v1", "u1", 100), note("b", "v2", "u2", 100)]
    out = N.assess(ns, panel("a", 200, [N.AGREE] * 5))                 # u1 has been paid once; u2 is waiting
    rows = N.earnings(out, N.ym(204))
    held = [claim("u1", "v3", until=10**12), claim("u3", "v4", until=10**12), claim("u4", "v5", until=1)]
    # earned 10, plus u2's waiting review and u3's held spot as first-evers (10 each) and u1's spot (5); u4's lapsed
    assert N.committed(rows, out, held, now=1000) == 1000 + 1000 + 500 + 1000
    assert N.spot_cost("u1", out) == 500 and N.spot_cost("u9", out) == 1000 and N.spot_cost("u9", out, {"u9"}) == 500


def test_the_pot_rolls_over_until_won_and_needs_20_with_quality():
    assert N.pot_cents(None) == 100_000 and N.pot_cents({"status": "won", "cents": 150_000}) == 100_000
    assert N.pot_cents({"status": "rolled", "cents": 100_000}) == 150_000
    assert N.pot_cents({"status": "rolled", "cents": 200_000}) == 200_000                    # capped
    row = lambda uid, counted, rated, agree: dict(N.blank(uid), counted=counted, rated=rated, agree=agree, judged=counted * 5)
    rows = {r["uid"]: r for r in (row("a", 25, 30, 110), row("b", 25, 26, 120), row("c", 19, 19, 95),
                                  row("d", 30, 40, 150), row("e", 40, 40, 200))}
    line = [r["uid"] for r in N.pot_line(rows, winners={"e"})]
    assert line == ["b", "a"]        # d is under 80%, c under 20, e won before; b wins the tie on agreement


def test_the_public_farm_check_names_a_shared_browser_or_network():
    assert N.risk_flags([], {}, shared=True) == ["Shares a browser or network with another partner"]
    assert N.risk_flags([], {}) == [] or os.path.exists(os.path.join(ROOT, "app", "rules_private.py"))


def test_three_of_five_does_not_qualify_and_the_panel_stops_at_five():
    ns = [note("a", "v1", "u1", 100)]
    js = panel("a", 200, [N.AGREE, N.AGREE, N.AGREE, N.DISAGREE, N.DISAGREE, N.AGREE, N.AGREE])
    a = N.assess(ns, js)["a"]
    assert (a["judged"], a["agree"], a["status"], a["cents"]) == (5, 3, "not_qualified", 0)


def test_cant_judge_is_recorded_but_never_counts():
    ns = [note("a", "v1", "u1", 100)]
    js = panel("a", 200, [N.CANT, N.CANT, N.AGREE, N.AGREE])
    a = N.assess(ns, js)["a"]
    assert (a["cant"], a["judged"], a["status"]) == (2, 2, "waiting")


def test_only_independent_readers_count():
    ns = [note("a", "v1", "u1", 100)]
    js = [judgment("a", "u1", 200),                                    # the writer
          judgment("a", "x1", 201, did_h="d-a"),                       # same browser
          judgment("a", "x2", 202, net_h="n-a"),                       # same network
          judgment("a", "x3", 50),                                     # before the note existed
          judgment("a", "ok", 203)]
    assert N.assess(ns, js)["a"]["judged"] == 1


def test_one_reader_stops_counting_after_three_notes_by_the_same_writer():
    ns = [note(f"n{i}", f"v{i}", "u1", 100 + i) for i in range(4)]
    js = [judgment(f"n{i}", "fan", 200 + i) for i in range(4)]
    out = N.assess(ns, js)
    assert [out[f"n{i}"]["judged"] for i in range(4)] == [1, 1, 1, 0]


def test_held_back_notes_count_for_nothing_and_anonymous_notes_never_earn():
    ns = [note("h", "v1", "u1", 50, mod="hidden"), note("a", "v1", None, 100, who="dabc"), note("b", "v1", "u2", 200)]
    js = panel("h", 300, [N.AGREE] * 5) + panel("a", 300, [N.AGREE] * 5) + panel("b", 400, [N.AGREE] * 5)
    out = N.assess(ns, js)
    assert "h" not in out
    assert out["a"]["first"] and out["a"]["cents"] == 0           # the anonymous note was the early find
    assert not out["b"]["first"] and out["b"]["cents"] == 500


def test_a_repeat_of_an_earlier_note_qualifies_but_earns_nothing():
    ns = [note("a", "v1", "u1", 100), note("b", "v1", "u2", 200, original=False)]
    js = panel("a", 300, [N.AGREE] * 4 + [N.DISAGREE]) + panel("b", 400, [N.AGREE] * 5)
    out = N.assess(ns, js)
    assert out["b"]["status"] == "qualified" and out["b"]["cents"] == 0 and out["a"]["first"]


def test_a_critical_note_qualifies_on_the_same_terms():
    ns = [note("s", "v1", "u1", 100, stance=N.SKIP)]
    out = N.assess(ns, panel("s", 200, [N.AGREE] * 4 + [N.DISAGREE]))
    assert out["s"]["status"] == "qualified" and out["s"]["cents"] == 500
    assert N.earnings(out, N.ym(204))["u1"]["total_cents"] == 1000          # and it can be a partner's first


def test_earnings_are_by_month_and_uncapped():
    ns = [note(n, f"v{n}", f"u{n}", 100) for n in "abc"]
    js = []
    for n in "abc":
        js += panel(n, 200, [N.AGREE] * 5)
    out = N.assess(ns, js)
    rows = N.earnings(out, N.ym(204))
    assert sum(r["total_cents"] for r in rows.values()) == 3000 and len(rows) == 3    # no pool to share out
    assert N.earnings(out, "1999-01") == {}


def test_record_summarises_a_writer():
    ns = [note("a", "v1", "u1", 100), note("b", "v2", "u1", 110), note("c", "v3", "u1", 120)]
    js = panel("a", 200, [N.AGREE] * 5) + panel("b", 200, [N.DISAGREE] * 5) + panel("c", 200, [N.AGREE])
    r = N.record(list(N.assess(ns, js).values()))
    assert r == {"notes": 3, "qualified": 1, "not_qualified": 1, "waiting": 1, "first": 1, "agreement": round(6 / 11, 2)}


def test_originality_is_the_closest_earlier_note():
    assert N.originality([1.0, 0.0], []) == 0.0
    assert N.originality([1.0, 0.0], [[0.0, 1.0], [0.6, 0.8]]) == 0.6


# ---------------- moderation ----------------
def fake_anthropic(monkeypatch, text=None, stop="end_turn", boom=False):
    class Msgs:
        def create(self, **kw):
            if boom:
                raise RuntimeError("down")
            return types.SimpleNamespace(stop_reason=stop, content=[types.SimpleNamespace(type="text", text=text or "")])

    class Client:
        def __init__(self):
            self.messages = Msgs()

    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=Client))


def test_moderation_outcomes(monkeypatch):
    assert N.moderate("") == ("ok", "")
    assert N.moderate("see www.spam.example")[0] == "hidden"
    fake_anthropic(monkeypatch, '{"ok": true}')
    assert N.moderate("Clear first lesson for people who know containers.") == ("ok", "")
    fake_anthropic(monkeypatch, '{"ok": false, "reason": "advertising"}')
    assert N.moderate("Buy my course now, best deal") == ("hidden", "advertising")
    fake_anthropic(monkeypatch, "no json here")
    assert N.moderate("Something about the video for beginners.")[0] == "pending"
    fake_anthropic(monkeypatch, boom=True)
    assert N.moderate("Something about the video for beginners.")[0] == "pending"
    fake_anthropic(monkeypatch, "", stop="refusal")
    assert N.moderate("Something about the video for beginners.")[0] == "hidden"


def test_only_a_hackers_review_earns_but_an_unpaid_note_can_be_the_early_find():
    ns = [note("free", "v1", "u1", 100, paid=False), note("hk", "v1", "u2", 200, paid=True)]
    js = panel("free", 300, [N.AGREE] * 5) + panel("hk", 400, [N.AGREE] * 4 + [N.DISAGREE])
    out = N.assess(ns, js)
    assert out["free"]["first"] and out["free"]["cents"] == 0
    assert not out["hk"]["first"] and out["hk"]["cents"] == 500


def test_review_moment_and_needs_checks():
    assert N.parse_moment("") is None and N.parse_moment("12:40") == 760 and N.parse_moment("1:02:03") == 3723
    with pytest.raises(ValueError):
        N.parse_moment("soon")
    assert N.fmt_moment(760) == "12:40" and N.fmt_moment(3723) == "1:02:03"
    good = "For people who know containers but keep tripping over localhost networking."
    assert N.check_review(good, "Basic Docker", 760, 1800) == ""
    assert N.check_review(good, "", 2000, 1800) != ""
    assert N.check_review(good, "see www.x.io", None, 1800) != ""


# ---------------- submitting a video that isn't listed ----------------
def yt_item(privacy="public", embeddable=True, rating="", live="none", dur="PT12M5S"):
    return {"id": "abcdefghijk", "status": {"privacyStatus": privacy, "embeddable": embeddable},
            "contentDetails": {"duration": dur, "contentRating": {"ytRating": rating} if rating else {}},
            "snippet": {"title": " A talk ", "channelTitle": "Chan", "channelId": "UC1", "publishedAt": "2026-10-01T00:00:00Z",
                        "liveBroadcastContent": live}, "statistics": {"viewCount": "1234"}}


def test_links_parse_to_a_video_id_and_shorts_are_refused():
    for link in ("https://www.youtube.com/watch?v=abcdefghijk", "https://youtu.be/abcdefghijk?t=9", "abcdefghijk",
                 "https://m.youtube.com/watch?list=PL1&v=abcdefghijk", "https://www.youtube.com/live/abcdefghijk",
                 "https://www.youtube-nocookie.com/embed/abcdefghijk"):
        assert N.parse_video_link(link) == ("abcdefghijk", ""), link
    assert N.parse_video_link("https://www.youtube.com/shorts/abcdefghijk") == ("", "shorts")
    assert N.parse_video_link("https://example.com/watch?v=1") == ("", "link")
    assert (N.parse_iso_dur("PT1H2M3S"), N.parse_iso_dur("PT59S"), N.parse_iso_dur("P1DT2H"), N.parse_iso_dur("nope")) == (3723, 59, 93600, 0)


def test_only_public_playable_full_length_videos_can_be_submitted():
    assert N.check_video(yt_item()) == ""
    assert N.check_video(None) and N.check_video(yt_item(privacy="unlisted"))
    assert N.check_video(yt_item(embeddable=False)) and N.check_video(yt_item(rating="ytAgeRestricted"))
    assert N.check_video(yt_item(live="live")) and N.check_video(yt_item(dur="PT2M59S"))
    assert N.video_fields(yt_item()) == {"title": "A talk", "channel": "Chan", "channel_id": "UC1", "published": "2026-10-01",
                                         "dur": 725, "views": 1234}


def test_weekly_picks_from_the_pool_only_take_videos_that_passed_the_topic_check():
    pool = [{"vid": f"v{i}", "channel": f"c{i}", "dur": 900} for i in range(40)]
    ok = {f"v{i}" for i in range(0, 40, 3)}
    vids, _ = N.pick_week("2026-11-09", pool, [], set(), 10, ok=ok)
    assert vids and set(vids) <= ok
    vids, _ = N.pick_week("2026-11-09", pool, [], set(), 10)
    assert len(vids) == 10


def test_the_topic_check_falls_back_when_the_model_cant_be_reached(monkeypatch):
    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))
    assert N.topic_fit([{"vid": "a", "title": "t"}], "LLMs") is None
    assert N.topic_fit([], "LLMs") == set()
