"""Videos people submitted on HackerTube AI that viewers then vouched for, as a community pack for the weekly
catalogue build (packs/viewers/videos.jsonl).

The build already merges any community pack the way it merges Stack Exchange: a base record from YouTube, the
pack's categories, and a comm["viewers"] entry with counts and a why line. So a vouched submission joins the
release, and with it search, topic pages, sitemaps and IndexNow, without changing the build script.

No review text and no names go into the pack: reviews stay blind until a viewer gives their own verdict, and
someone who deletes their reviews isn't left quoted in the catalogue."""
import collections, json, os

from . import notes as nts

COMMUNITY = "viewers"
LABEL = "HackerTube viewers"
SHORT = "viewer"


def pack_rows(added, notes, verdicts, judgments):
    """One pack row per vouched submission that still has a review that counts. `added` are ht_added docs;
    `notes`, `verdicts` and `judgments` are the docs for those videos."""
    assessed = nts.assess(notes, judgments)
    by_vid = collections.defaultdict(lambda: {"notes": [], "verdicts": []})
    for n in notes:
        if nts.counts(n):
            by_vid[n["vid"]]["notes"].append(n)
    for x in verdicts:
        by_vid[x["vid"]]["verdicts"].append(x)
    rows = []
    for d in sorted(added, key=lambda d: float(d.get("ts", 0))):
        vid = d.get("vid", "")
        if d.get("status") != "vouched" or not nts.VID_RE.match(vid):
            continue
        counted = [a for a in assessed.values() if a["vid"] == vid and a["status"] == "qualified"]
        if not counted:
            continue  # the review that vouched for it has since been deleted or held back
        best = max(counted, key=lambda a: (a["agree"], -a["ts"]))
        ns, vs = by_vid[vid]["notes"], by_vid[vid]["verdicts"]
        recs = [x for x in ns if x.get("stance") == nts.REC] + [x for x in vs if x.get("verdict") == nts.REC]
        people = {nts.person(x) for x in recs if nts.person(x)}
        months = sorted({nts.ym(x["ts"]) for x in recs} or {nts.ym(d.get("ts", 0))})
        why = (f"Submitted by a viewer and vouched for here: {best['agree']} of the first {nts.PANEL} viewers who "
               f"watched it agreed with the review that brought it in.")
        rows.append({"vid": vid, "community": COMMUNITY, "title": d.get("title", ""), "channel": d.get("channel", ""),
                     "categories": [d["topic"]] if d.get("topic") else [], "why": why,
                     "stats": {"recommenders": max(len(people), 1), "rec_months": len(months), "mentions": len(ns),
                               "first_year": int(months[0][:4]), "last_year": int(months[-1][:4])},
                     "score": max(len(people), 1) + best["agree"]})
    return rows


def write(path, rows):
    """Write the pack, always: an empty file clears last week's rows for videos that are no longer vouched."""
    os.makedirs(path, exist_ok=True)
    with open(os.path.join(path, "videos.jsonl"), "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def from_firestore(db):
    """Read what the pack needs: vouched submissions, and the reviews, verdicts and ratings on those videos."""
    from google.cloud.firestore_v1.base_query import FieldFilter

    def docs(coll, field, value):
        return [dict(s.to_dict(), id=s.id) for s in db.collection(coll).where(filter=FieldFilter(field, "==", value)).stream()]

    added = docs(nts.ADDED_COLL, "status", "vouched")
    notes, verdicts, judgments = [], [], []
    for d in added:
        notes += docs(nts.NOTES_COLL, "vid", d["vid"])
        verdicts += docs(nts.VERDICTS_COLL, "vid", d["vid"])
        judgments += docs(nts.JUDGMENTS_COLL, "vid", d["vid"])
    return pack_rows(added, notes, verdicts, judgments)
