"""Write two unique paragraphs for every topic page from what HN actually recommends there.
Output: app/data/topic_intros.json  {cid: {"p1": ..., "p2": ...}}. Re-run with --only cid to redo one.
Needs ANTHROPIC_API_KEY. Skips topics already in the file unless --force."""
import json, os, sys, threading, queue, logging
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import anthropic
from app.catalog import Catalog

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("intros")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
OUT = os.path.join(os.path.dirname(__file__), "..", "app", "data", "topic_intros.json")
SCHEMA = {"type": "object", "properties": {"p1": {"type": "string"}, "p2": {"type": "string"}},
          "required": ["p1", "p2"], "additionalProperties": False}
PROMPT = """You write the short introduction at the top of a topic page on HackerTube AI, a site that lists the YouTube videos Hacker News and Stack Exchange commenters recommend to each other, ranked by how many separate months they were recommended.

Topic: {name} (area: {area})
Taxonomy description: {desc}
Videos in this topic: {count}

Top videos (title | channel | year | number of recommending comments):
{videos}

Why commenters recommend some of them:
{whys}

Write two paragraphs of plain, specific prose for a reader deciding whether to spend an evening here.
Paragraph 1 (70-110 words): what this list actually contains and why Hacker News keeps recommending it. Name three or four of the videos exactly as titled. Say what kind of recommendation they are (answers to a recurring question, a talk people cite, a lecture series people finish).
Paragraph 2 (60-90 words): where to start and how the list runs: which one is the quick first watch, which is the long commitment, what the rest covers. Mention a length or a year where it helps.

Rules: no hype words, no "dive", no "journey", no exclamation marks, no em dashes, no lists, no headings, no quotes around titles. Do not invent facts about the videos beyond the titles and reasons given. Do not mention HackerTube AI, Pro, or the site itself. Write as a knowledgeable person would, not as a brochure."""


def topic_input(cat, c):
    vids = sorted((cat.by_vid[x] for x in c["vids"]), key=lambda v: -v["score"])[:14]
    lines = [f"{v['title']} | {v['channel']} | {v['year']} | {v['recs']}" for v in vids]
    whys = []
    for v in vids[:8]:
        if v.get("why_hn"):
            whys.append(f"{v['title']}: {v['why_hn'][:220]}")
    return PROMPT.format(name=c["name"], area=c["area"], desc=c["description"][:300], count=len(c["vids"]),
                         videos="\n".join(lines), whys="\n".join(whys) or "(none recorded)")


def gen(client, text):
    r = client.messages.create(model=MODEL, max_tokens=2000,
                               output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
                               messages=[{"role": "user", "content": text}])
    raw = "".join(getattr(b, "text", "") for b in r.content if getattr(b, "type", "") == "text")
    try:
        d = json.loads(raw)
    except ValueError:
        log.warning("bad json: stop=%s out_tokens=%s raw=%r", r.stop_reason, r.usage.output_tokens, raw[:120])
        raise
    for k in ("p1", "p2"):
        d[k] = d[k].replace("—", ",").replace("–", "-").strip()
    return d


def main():
    cat = Catalog()
    cats = [c for a in cat.areas for c in a["cats"]]
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    done = {} if "--force" in sys.argv else (json.load(open(OUT)) if os.path.exists(OUT) else {})
    todo = [c for c in cats if (c["id"] == only if only else c["id"] not in done)]
    log.info("topics %d, to write %d", len(cats), len(todo))
    client = anthropic.Anthropic()
    q = queue.Queue(); [q.put(c) for c in todo]
    lock = threading.Lock()

    def worker():
        while True:
            try: c = q.get_nowait()
            except queue.Empty: return
            for attempt in range(3):
                try:
                    d = gen(client, topic_input(cat, c)); break
                except Exception as e:  # noqa: BLE001
                    log.warning("%s attempt %d failed: %s", c["id"], attempt + 1, e); d = None
            if d:
                with lock:
                    done[c["id"]] = d
                    json.dump(done, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
                log.info("ok %s (%d words)", c["id"], len((d["p1"] + d["p2"]).split()))
    ts = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in ts]; [t.join() for t in ts]
    log.info("written %d/%d", len(done), len(cats))


if __name__ == "__main__":
    main()
