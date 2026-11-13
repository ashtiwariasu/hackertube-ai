"""W1 quality gate: does a web-cited YouTube video belong in HackerTube, what kind is it, which area.
Usage: python3 -I web_gate.py META.jsonl TRANCO3.tsv OUT.jsonl [--ids FILE] [--workers 96]
Resumable: ids already in OUT are skipped. Reads title/channel/desc/duration/category/lang + citing domains."""
import json, sys, time, threading, urllib.request, queue, os, re
URL = "http://127.0.0.1:8100/v1/chat/completions"
AREAS = ["software", "systems-security", "ai-data", "math-theory", "hardware", "computing-history-culture",
         "design-graphics-media", "business-economics", "politics-history-society", "physics-space",
         "life-sciences-health", "earth-energy-environment", "engineering-making", "games", "arts-entertainment",
         "ideas-education", "none"]
KINDS = ["talk", "lecture", "explainer", "documentary", "tutorial", "interview", "demo", "performance",
         "music_video", "trailer_promo", "news_clip", "entertainment_clip", "vlog", "short_clip", "other"]
SCHEMA = {"type": "object", "properties": {
    "kind": {"type": "string", "enum": KINDS},
    "keep": {"type": "boolean"},
    "english": {"type": "boolean"},
    "area": {"type": "string", "enum": AREAS},
    "why": {"type": "string"}},
    "required": ["kind", "keep", "english", "area", "why"], "additionalProperties": False}
SYSTEM = """You curate a library of YouTube videos worth a curious adult's time: talks, lectures, explainers, documentaries, tutorials, interviews, demonstrations and performances of lasting value. Each candidate was linked by several independent, widely read websites. Judge it from its metadata only.

kind: the closest of talk, lecture, explainer, documentary, tutorial, interview, demo, performance, music_video, trailer_promo, news_clip, entertainment_clip, vlog, short_clip, other.
keep: true when a curious adult who has never heard of the channel would be glad to have been pointed to it and it will still be worth watching in five years. false for music videos and songs, trailers, adverts and product promotion, news clips about passing events, memes and reaction videos, vlogs, sports highlights, gameplay without commentary of substance, anything under about three minutes, and anything whose value is only to fans of a specific show, game or celebrity. A performance counts only when the recording itself is widely regarded as significant (a landmark concert, a famous session), not merely an official live clip or a cover.
english: true when the video is in English or its title and description are in English.
area: the one library area it belongs to, or none when kept=false or nothing fits:
software (programming, languages, web, architecture), systems-security (OS, networks, security, databases, cloud), ai-data (AI, ML, data science), math-theory (mathematics, theoretical CS), hardware (electronics, chips, retro hardware), computing-history-culture (computing history, hacker culture, open source), design-graphics-media (design, graphics, typography, photography, film craft), business-economics (business, startups, economics, finance, management), politics-history-society (politics, history, law, society, philosophy of society), physics-space (physics, astronomy, space, chemistry), life-sciences-health (biology, medicine, neuroscience, psychology), earth-energy-environment (earth science, climate, energy, environment), engineering-making (engineering, transport, manufacturing, making, DIY), games (games, game design), arts-entertainment (music as a subject, comedy, film and literature as subjects, performances of lasting value), ideas-education (education, language, big ideas, general lectures).
why: one sentence of at most 25 words saying what the video shows or argues; no hype, no 'this video'.

Return JSON with kind, keep, english, area, why."""
USER = """Title: {title}
Channel: {channel}
Duration: {duration}   YouTube category: {category}   Language tag: {lang}   Views: {views}
Linked by: {domains}
Description: {desc}"""
CATS = {"1": "Film", "2": "Autos", "10": "Music", "15": "Pets", "17": "Sports", "19": "Travel", "20": "Gaming", "22": "People & Blogs",
        "23": "Comedy", "24": "Entertainment", "25": "News & Politics", "26": "Howto & Style", "27": "Education", "28": "Science & Technology", "29": "Nonprofits"}

def dur(s):
    m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s or "")
    if not m: return "?"
    h, mi, se = (int(x or 0) for x in m.groups())
    return f"{h}h{mi:02d}m" if h else f"{mi}m{se:02d}s"

def call(r, domains):
    msg = USER.format(title=r.get("title") or "", channel=r.get("channel") or "", duration=dur(r.get("duration")),
                      category=CATS.get(str(r.get("category")), r.get("category")), lang=r.get("lang") or "unknown",
                      views=r.get("views") or "?", domains=", ".join(domains[:8]), desc=(r.get("desc") or "")[:600].replace("\n", " "))
    body = {"model": "labeller", "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": msg}],
            "max_tokens": 160, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_schema", "json_schema": {"name": "gate", "schema": SCHEMA}}}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                out = json.load(resp)
            return json.loads(out["choices"][0]["message"]["content"])
        except Exception as e:  # noqa: BLE001
            err = e; time.sleep(2 * (attempt + 1))
    return {"error": str(err)}

def main():
    meta_p, tranco_p, out_p = sys.argv[1:4]
    only = set(open(sys.argv[sys.argv.index("--ids") + 1]).read().split()) if "--ids" in sys.argv else None
    workers = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 96
    domains = {}
    for line in open(tranco_p):
        p = line.rstrip("\n").split("\t")
        if len(p) >= 3: domains[p[0]] = p[2].split(",")
    done = set()
    if os.path.exists(out_p):
        for line in open(out_p):
            try: done.add(json.loads(line)["vid"])
            except Exception: pass
    rows = []
    for line in open(meta_p):
        r = json.loads(line)
        if not r.get("title") or r["id"] in done or (only and r["id"] not in only): continue
        rows.append(r)
    print("todo", len(rows), "done", len(done), flush=True)
    q = queue.Queue(); [q.put(r) for r in rows]
    lock = threading.Lock(); out = open(out_p, "a"); n = [0]; t0 = time.time()
    def work():
        while True:
            try: r = q.get_nowait()
            except queue.Empty: return
            lab = call(r, domains.get(r["id"], []))
            with lock:
                out.write(json.dumps({"vid": r["id"], **lab}) + "\n"); out.flush(); n[0] += 1
                if n[0] % 500 == 0: print(n[0], f"{n[0]/(time.time()-t0):.1f}/s", flush=True)
    ts = [threading.Thread(target=work) for _ in range(workers)]; [t.start() for t in ts]; [t.join() for t in ts]
    print("done", n[0], f"{time.time()-t0:.0f}s", flush=True)

if __name__ == "__main__":
    main()
