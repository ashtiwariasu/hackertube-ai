"""Qualify labelled videos, fetch YouTube metadata, write cards with claude-sonnet-5-5 Batches, emit the pack.
Usage (keys in env: ANTHROPIC_API_KEY, YOUTUBE_API_KEY):
  python -I scripts/06_cards.py qualify  work/ taxonomy.json   # stats per (community, video) -> work/qualified.json
  python -I scripts/06_cards.py ytmeta   work/ taxonomy.json   # videos.list for qualified vids -> work/yt_meta.jsonl
  python -I scripts/06_cards.py estimate work/ taxonomy.json   # token + US$ estimate for the card batch
  python -I scripts/06_cards.py submit   work/ taxonomy.json [so_from so_to]   # all non-SO + SO slice by authors
  python -I scripts/06_cards.py collect  work/ taxonomy.json   # poll, write work/cards.jsonl + spend
  python -I scripts/06_cards.py write    work/ taxonomy.json out_dir/   # videos.jsonl + mentions.jsonl
Mentions Haiku marks sample_url (the link is only example data in code, regex tests, embed demos) are dropped.
Qualification: within one community, counted mentions (>= 100 chars of own text) labelled strong_rec, rec or
reference must come from >= 3 distinct authors in >= 3 distinct calendar months (critical and joke mentions do
not make a video qualify). Stats are computed over all counted mentions.
"""
import collections, json, os, re, sys, time, urllib.parse, urllib.request

cmd, work, taxp = sys.argv[1], sys.argv[2], sys.argv[3]
HERE = os.path.dirname(os.path.abspath(__file__))
SITES = json.load(open(os.path.join(HERE, "se_sites.json"), encoding="utf-8"))
MODEL = "claude-sonnet-5-5"
PRICE_IN, PRICE_OUT = 2.0 / 2 / 1e6, 10.0 / 2 / 1e6     # batch = 50%
PRICE_CW, PRICE_CR = PRICE_IN * 1.25, PRICE_IN * 0.1
RANK = {"strong_rec": 0, "rec": 1, "reference": 2, "critical": 3, "joke": 4}
POINTING = {"strong_rec", "rec", "reference"}
SE_NAMES = {"stackoverflow": "Stack Overflow", "superuser": "Super User", "serverfault": "Server Fault",
            "askubuntu": "Ask Ubuntu", "mathoverflow": "MathOverflow", "math": "Mathematics", "diy": "DIY",
            "scifi": "Science Fiction & Fantasy", "rpg": "RPG", "ux": "User Experience", "gamedev": "Game Development"}


def community_label(c):          # same naming the site's merger uses (scripts/build_catalog.py)
    site = c[3:]
    name = SE_NAMES.get(site, site.replace("-", " ").title())
    return name if site in ("stackoverflow", "superuser", "serverfault", "askubuntu", "mathoverflow") else f"{name} Stack Exchange"


# Display names for sites whose short name title-cases badly. The cards were prompted with community_label(); the
# "why" sentence is rewritten with these names when written. Suggested additions for SE_NAMES in the merger.
NICE = {"3dprinting": "3D Printing", "ai": "AI", "avp": "Video Production", "cogsci": "Cognitive Sciences",
        "codegolf": "Code Golf", "codereview": "Code Review", "computergraphics": "Computer Graphics",
        "crypto": "Cryptography", "cs": "Computer Science", "cstheory": "Theoretical Computer Science",
        "datascience": "Data Science", "dba": "Database Administrators", "dsp": "Signal Processing",
        "earthscience": "Earth Science", "eosio": "EOS.IO", "gis": "GIS", "graphicdesign": "Graphic Design",
        "hsm": "History of Science and Mathematics", "matheducators": "Mathematics Educators",
        "martialarts": "Martial Arts", "musicfans": "Music Fans", "opendata": "Open Data", "opensource": "Open Source",
        "pm": "Project Management", "quantumcomputing": "Quantum Computing", "raspberrypi": "Raspberry Pi",
        "reverseengineering": "Reverse Engineering", "softwareengineering": "Software Engineering",
        "sqa": "Software Quality Assurance", "tex": "TeX - LaTeX", "unix": "Unix & Linux", "vi": "Vi and Vim",
        "arduino": "Arduino", "sharepoint": "SharePoint"}
NICE_FULL = {"es.stackoverflow": "Stack Overflow en español", "pt.stackoverflow": "Stack Overflow em Português",
             "ru.stackoverflow": "Stack Overflow in Russian", "ja.stackoverflow": "Stack Overflow in Japanese"}


def nice_label(c):
    site = c[3:]
    if site in NICE_FULL:
        return NICE_FULL[site]
    if site in NICE:
        return f"{NICE[site]} Stack Exchange"
    return community_label(c)


def jl(p):
    return [json.loads(l) for l in open(p, encoding="utf-8")] if os.path.exists(p) else []


def load_mentions():
    ms = jl(os.path.join(work, "candidates.jsonl"))
    labels = {l["cid"]: l for l in jl(os.path.join(work, "labels.jsonl"))}
    out = collections.defaultdict(list)
    for i, m in enumerate(ms):
        lab = labels.get(f"m{i}")
        if not lab or lab.get("sample_url"):
            continue
        q = (lab.get("quote") or "").strip()
        if q and q not in m["text"][:1500]:
            q = ""
        why = "" if lab["stance"] in ("joke", "critical") else " ".join(lab.get("why", "").split()[:15])
        m.update(stance=lab["stance"], why=why, quote=q)
        out[(m["community"], m["vid"])].append(m)
    return out


def stats_for(ms):
    recs = [m for m in ms if m["stance"] in ("rec", "strong_rec")]
    strong_months = {m["month"] for m in ms if m["stance"] == "strong_rec"}
    rec_months = {m["month"] for m in recs}
    st = collections.Counter(m["stance"] for m in ms)
    years = [int(m["month"][:4]) for m in ms]
    s = {"mentions": len(ms), "authors": len({m["author_key"] for m in ms}),
         "recommenders": len({m["author_key"] for m in recs}), "rec_months": len(rec_months),
         "strong_recs": st["strong_rec"], "stances": {k: st[k] for k in RANK},
         "first_year": min(years), "last_year": max(years)}
    return s, len(rec_months) + 0.5 * len(strong_months)


def pick(ms, n=20):
    ms = sorted(ms, key=lambda m: (RANK[m["stance"]], -m["score"], m["month"]))
    first, rest, seen = [], [], set()
    for m in ms:
        (rest if m["author_key"] in seen else first).append(m)
        seen.add(m["author_key"])
    return (first + rest)[:n]


def qualified():
    return json.load(open(os.path.join(work, "qualified.json")))


def yt_ok():
    ok = {}
    for r in jl(os.path.join(work, "yt_meta.jsonl")):
        it = r["item"]
        if it and it.get("status", {}).get("privacyStatus") == "public" and it.get("status", {}).get("embeddable", False) \
                and it.get("status", {}).get("uploadStatus", "processed") == "processed":
            ok[r["vid"]] = it
    return ok


def taxonomy():
    t = json.load(open(taxp, encoding="utf-8"))
    return [(c["id"], c["name"]) for a in t["areas"] for c in a["categories"]]


def system_prompt():
    cats = "\n".join(f"{i} | {n}" for i, n in taxonomy())
    return f"""You write catalogue cards for hackertube.ai, a site that lists YouTube videos that online communities keep recommending. Each card covers one video as recommended in one Stack Exchange community. You get the video's YouTube metadata and up to 20 posts or comments from that community that link it (the video is [VIDEO] in their text).

Return JSON:
- summary: 2-3 sentences on what the video is (subject, format, who presents it). Plain, factual, no hype.
- why: one sentence on why this community points people to it. Start with the community's name exactly as given (e.g. "Cooking Stack Exchange answerers point people to it for ..."; "Stack Overflow users link it when ..."). Base it on the mentions.
- content_type: talk, lecture, course, documentary, explainer, interview, podcast, tutorial, demo, news, commentary, comedy, music or other.
- level: general, intro, intermediate or advanced.
- shelf_life: evergreen, dated or news.
- primary_topic: a short phrase.
- tags: 3-6 short lowercase tags.
- categories: 1 to 3 category ids from the list below, best first. Use ids exactly as written.
- quote_mention_id: the id of the mention whose text has the best short line about the video, or "" if none is good.
- quote_text: an excerpt under 200 characters copied exactly from that mention's text (no [VIDEO] or [LINK]), or "".

Write in plain English, no em dashes.

Category list (id | name):
{cats}"""


def schema():
    ids = [i for i, _ in taxonomy()]
    return {"type": "object", "properties": {
        "summary": {"type": "string"}, "why": {"type": "string"},
        "content_type": {"type": "string", "enum": ["talk", "lecture", "course", "documentary", "explainer", "interview", "podcast",
                                                     "tutorial", "demo", "news", "commentary", "comedy", "music", "other"]},
        "level": {"type": "string", "enum": ["general", "intro", "intermediate", "advanced"]},
        "shelf_life": {"type": "string", "enum": ["evergreen", "dated", "news"]},
        "primary_topic": {"type": "string"}, "tags": {"type": "array", "items": {"type": "string"}},
        "categories": {"type": "array", "items": {"type": "string"}},
        "quote_mention_id": {"type": "string"}, "quote_text": {"type": "string"}},
        "required": ["summary", "why", "content_type", "level", "shelf_life", "primary_topic", "tags", "categories",
                     "quote_mention_id", "quote_text"], "additionalProperties": False}


def dur(iso):
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return "?"
    d, h, mi, s = (int(x or 0) for x in m.groups())
    tot = d * 86400 + h * 3600 + mi * 60 + s
    return f"{tot // 3600}h{tot % 3600 // 60:02d}m" if tot >= 3600 else f"{tot // 60}m{tot % 60:02d}s"


def card_user(q, it):
    sn, cd = it.get("snippet", {}), it.get("contentDetails", {})
    host = q["site"]
    official = SITES.get(host, {}).get("name", host)
    lines = [f"Community: {community_label(q['community'])} (site {host}, official name \"{official}\")",
             f"Video title: {sn.get('title', '')}", f"Channel: {sn.get('channelTitle', '')}",
             f"Duration: {dur(cd.get('duration'))}", f"Published: {sn.get('publishedAt', '')[:10]}",
             f"Description (first 1500 chars):\n{(sn.get('description') or '')[:1500]}", "",
             f"Mentions ({len(q['picked'])} of {q['stats']['mentions']}):"]
    for m in q["picked"]:
        lines.append(f"[{m['id']}] {m['kind']} by {m['author']}, {m['month']}, stance {m['stance']}, on \"{m['context']}\":\n{m['text'][:700]}")
    return "\n\n".join(lines[:7]) + "\n" + "\n\n".join(lines[7:])


def card_params(q, it, sysp, sch):
    return {"model": MODEL, "max_tokens": 1500,
            "system": [{"type": "text", "text": sysp, "cache_control": {"type": "ephemeral"}}],
            "thinking": {"type": "between_tools"},
            "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": sch}},
            "messages": [{"role": "user", "content": card_user(q, it)}]}


def card_jobs():
    ok = yt_ok()
    jobs = []
    for i, q in enumerate(qualified()):
        if q["vid"] in ok:
            jobs.append((f"v{i}", q, ok[q["vid"]]))
    return jobs


if cmd == "qualify":
    by = load_mentions()
    out, counts = [], collections.defaultdict(collections.Counter)
    for (c, vid), ms in by.items():
        counts[c]["labelled_videos"] += 1
        pointing = [m for m in ms if m["stance"] in POINTING]
        if len({m["author_key"] for m in pointing}) < 3 or len({m["month"] for m in pointing}) < 3:
            continue
        st, score = stats_for(ms)
        counts[c]["qualified_videos"] += 1
        out.append({"community": c, "vid": vid, "site": ms[0]["site"], "stats": st, "score": score, "picked": pick(ms)})
    out.sort(key=lambda q: -q["score"])
    json.dump(out, open(os.path.join(work, "qualified.json"), "w"), ensure_ascii=False)
    json.dump({c: dict(v) for c, v in counts.items()}, open(os.path.join(work, "qualify_counts.json"), "w"), indent=1)
    print("qualified", len(out), "unique vids", len({q["vid"] for q in out}))

elif cmd == "ytmeta":
    key = os.environ["YOUTUBE_API_KEY"]
    vids = sorted({q["vid"] for q in qualified()})
    path = os.path.join(work, "yt_meta.jsonl")
    done = {r["vid"] for r in jl(path)}
    todo = [v for v in vids if v not in done]
    print("vids", len(vids), "todo", len(todo), flush=True)
    with open(path, "a", encoding="utf-8") as f:
        for i in range(0, len(todo), 50):
            ids = todo[i:i + 50]
            qs = urllib.parse.urlencode({"part": "snippet,contentDetails,statistics,status", "id": ",".join(ids), "key": key})
            for attempt in range(5):
                try:
                    with urllib.request.urlopen("https://www.googleapis.com/youtube/v3/videos?" + qs, timeout=60) as r:
                        items = json.load(r).get("items", [])
                    break
                except Exception as e:
                    print("retry", attempt, type(e).__name__, flush=True)
                    time.sleep(3 * (attempt + 1))
            else:
                raise SystemExit("youtube gave up")
            got = {it["id"]: it for it in items}
            for v in ids:
                f.write(json.dumps({"vid": v, "item": got.get(v)}) + "\n")
            time.sleep(0.1)
    print("usable", len(yt_ok()), "of", len(vids))

elif cmd in ("estimate", "submit"):
    import anthropic
    client = anthropic.Anthropic()
    sysp, sch = system_prompt(), schema()
    jobs = card_jobs()
    if cmd == "estimate":
        import random
        sample = random.Random(2).sample(jobs, min(30, len(jobs)))
        fmt = {"format": {"type": "json_schema", "schema": sch}}
        sys_t = client.messages.count_tokens(model=MODEL, system=sysp, output_config=fmt,
                                             messages=[{"role": "user", "content": "x"}]).input_tokens
        user_t = 0
        for cid, q, it in sample:
            user_t += client.messages.count_tokens(model=MODEL, system=sysp, output_config=fmt,
                                                   messages=[{"role": "user", "content": card_user(q, it)}]).input_tokens - sys_t
        avg_user = user_t / len(sample)
        n = len(jobs)
        no_cache = n * (sys_t + avg_user) * PRICE_IN
        out_usd = n * 380 * PRICE_OUT
        print(json.dumps({"cards": n, "system_tokens": sys_t, "avg_user_tokens": round(avg_user),
                          "est_usd_no_cache": round(no_cache + out_usd, 2),
                          "est_usd_cache": round(n * sys_t * PRICE_CR + n * avg_user * PRICE_IN + out_usd, 2)}))
    else:
        # Stack Overflow is capped by distinct-author count (budget guard): pass so_from so_to to pick a slice
        so_from = int(sys.argv[4]) if len(sys.argv) > 4 else 0
        so_to = int(sys.argv[5]) if len(sys.argv) > 5 else 10 ** 9
        done = {c["cid"] for c in jl(os.path.join(work, "cards.jsonl"))}
        so = sorted([j for j in jobs if j[1]["community"] == "se:stackoverflow"],
                    key=lambda j: (-j[1]["stats"]["authors"], -j[1]["score"]))
        rest = [j for j in jobs if j[1]["community"] != "se:stackoverflow"] if so_from == 0 else []
        jobs = [j for j in rest + so[so_from:so_to] if j[0] not in done]
        bp = os.path.join(work, "card_batches.json")
        ids = json.load(open(bp)) if os.path.exists(bp) else []
        for i in range(0, len(jobs), 10000):
            chunk = jobs[i:i + 10000]
            b = client.messages.batches.create(requests=[{"custom_id": cid, "params": card_params(q, it, sysp, sch)}
                                                         for cid, q, it in chunk])
            ids.append(b.id)
            print("submitted", b.id, len(chunk), "so slice", so_from, so_to, flush=True)
        json.dump(ids, open(bp, "w"))
        json.dump([cid for cid, q, it in so], open(os.path.join(work, "so_rank.json"), "w"))

elif cmd == "collect":
    import anthropic
    client = anthropic.Anthropic()
    cp = os.path.join(work, "card_batches_collected.json")
    collected = json.load(open(cp)) if os.path.exists(cp) else []
    ids = [i for i in json.load(open(os.path.join(work, "card_batches.json"))) if i not in collected]
    while True:
        st = [client.messages.batches.retrieve(i) for i in ids]
        print(time.strftime("%H:%M:%S"), [(s.processing_status, s.request_counts.succeeded, s.request_counts.processing) for s in st], flush=True)
        if all(s.processing_status == "ended" for s in st):
            break
        time.sleep(60)
    tin = tcw = tcr = tout = 0
    ok = bad = 0
    with open(os.path.join(work, "cards.jsonl"), "a", encoding="utf-8") as out:
        for i in ids:
            for r in client.messages.batches.results(i):
                if r.result.type != "succeeded":
                    bad += 1
                    continue
                msg = r.result.message
                u = msg.usage
                tin += u.input_tokens
                tcw += u.cache_creation_input_tokens or 0
                tcr += u.cache_read_input_tokens or 0
                tout += u.output_tokens
                txt = next((b.text for b in msg.content if b.type == "text"), "")
                try:
                    card = json.loads(txt)
                except Exception:
                    bad += 1
                    continue
                ok += 1
                out.write(json.dumps({"cid": r.custom_id, **card}, ensure_ascii=False) + "\n")
    usd = tin * PRICE_IN + tcw * PRICE_CW + tcr * PRICE_CR + tout * PRICE_OUT
    sp = os.path.join(work, "spend.json")
    spend = json.load(open(sp)) if os.path.exists(sp) else {}
    spend.setdefault("cards", []).append({"model": MODEL, "batches": ids, "ok": ok, "failed": bad, "input_tokens": tin,
                                          "cache_write_tokens": tcw, "cache_read_tokens": tcr, "output_tokens": tout,
                                          "usd": round(usd, 4)})
    json.dump(spend, open(sp, "w"), indent=1)
    json.dump(collected + ids, open(cp, "w"))
    print("cards ok", ok, "failed", bad, "usd", round(usd, 4))

elif cmd == "write":
    outd = sys.argv[4]
    os.makedirs(outd, exist_ok=True)
    cards = {c["cid"]: c for c in jl(os.path.join(work, "cards.jsonl"))}
    valid = {i for i, _ in taxonomy()}
    nv = nm = 0
    with open(os.path.join(outd, "videos.jsonl"), "w", encoding="utf-8") as fv, \
            open(os.path.join(outd, "mentions.jsonl"), "w", encoding="utf-8") as fm:
        for cid, q, it in card_jobs():
            c = cards.get(cid)
            if not c:
                continue
            sn = it["snippet"]
            quote = None
            qm = next((m for m in q["picked"] if m["id"] == c.get("quote_mention_id")), None)
            qt = (c.get("quote_text") or "").strip()
            if qm and qt and qt in qm["text"]:
                quote = {"text": qt, "mention_id": qm["id"], "year": qm["month"][:4]}
            cats = []
            for x in c["categories"]:
                if x in valid and x not in cats:
                    cats.append(x)
            why = c["why"]
            if nice_label(q["community"]) != community_label(q["community"]):
                why = why.replace(community_label(q["community"]), nice_label(q["community"]))
            fv.write(json.dumps({
                "vid": q["vid"], "community": q["community"], "title": sn.get("title", ""), "channel": sn.get("channelTitle", ""),
                "summary": c["summary"], "why": why, "content_type": c["content_type"], "level": c["level"],
                "shelf_life": c["shelf_life"], "primary_topic": c["primary_topic"], "tags": c["tags"][:8],
                "categories": cats[:3], "quote": quote, "stats": q["stats"], "score": q["score"]}, ensure_ascii=False) + "\n")
            nv += 1
            for m in q["picked"]:
                fm.write(json.dumps({
                    "id": m["id"], "url": m["url"], "vid": m["vid"], "community": m["community"], "author": m["author"],
                    "month": m["month"], "context": m["context"], "stance": m["stance"], "why": m["why"], "quote": m["quote"],
                    "text": m["text"][:1500]}, ensure_ascii=False) + "\n")
                nm += 1
    print("videos", nv, "mentions", nm)
