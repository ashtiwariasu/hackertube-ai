"""Stance-label candidate mentions with claude-haiku-5-5 via the Message Batches API.
Usage (ANTHROPIC_API_KEY in env):
  python -I scripts/05_label.py estimate work/        # token estimate + US$ (no spend beyond count_tokens)
  python -I scripts/05_label.py submit   work/        # submit batches -> work/label_batches.json
  python -I scripts/05_label.py collect  work/        # poll until ended, write work/labels.jsonl + spend
"""
import json, os, sys, time, random
import anthropic

MODEL = "claude-haiku-5-5"
PRICE_IN, PRICE_OUT = 0.10 / 2 / 1e6, 0.50 / 2 / 1e6     # batch = 50% of $0.10 / $0.50 per MTok
cmd, work = sys.argv[1], sys.argv[2]
client = anthropic.Anthropic()

SYSTEM = """You label how a Stack Exchange post or comment uses a YouTube video. The video is marked [VIDEO] in the text; other links are [LINK]. You only see the text, not the video.

Return JSON with:
- stance: one of
  strong_rec = enthusiastic recommendation (e.g. "this talk is excellent", "best explanation I have seen", "must watch").
  rec = points the reader to the video because watching it helps: it explains, demonstrates, teaches or answers something, even when phrased plainly ("see this video", "X explains it here", "this tutorial shows how").
  reference = cites the video without suggesting the reader watch it to learn: as a source for a single claim or quote, an example or news clip, or the thing being asked about (most questions that link a video are reference).
  critical = criticises, disputes or debunks the video.
  joke = meme, punchline or off-topic aside.
- why: at most 15 words saying what a viewer gets from the video according to this text (e.g. "Shows step by step how to temper chocolate without a thermometer."). Use "" for joke or critical.
- sample_url: true when the video link is only example data and its content does not matter: test input for a URL parser or regex, a placeholder video in embed or player code, an arbitrary video used to demonstrate an API or bug. Otherwise false.
- quote: a short excerpt (under 200 characters) copied exactly, character for character, from the text, that best says what the video offers or why it is worth watching. Do not include [VIDEO] or [LINK]. Use "" if nothing fits."""

SCHEMA = {"type": "object", "properties": {
    "stance": {"type": "string", "enum": ["strong_rec", "rec", "reference", "critical", "joke"]},
    "why": {"type": "string"}, "sample_url": {"type": "boolean"}, "quote": {"type": "string"}},
    "required": ["stance", "why", "sample_url", "quote"], "additionalProperties": False}


def site_label(m):
    host = m["site"]
    return host


def user_msg(m):
    return (f"Site: {site_label(m)}\nKind: {m['kind']}\nQuestion title: {m['context']}\n\n"
            f"Text:\n{m['text'][:1500]}")


def params(m):
    return {"model": MODEL, "max_tokens": 400, "system": SYSTEM,
            "thinking": {"type": "disabled"},
            "output_config": {"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
            "messages": [{"role": "user", "content": user_msg(m)}]}


def load():
    ms = [json.loads(l) for l in open(os.path.join(work, "candidates.jsonl"), encoding="utf-8")]
    for i, m in enumerate(ms):
        m["cid"] = f"m{i}"
    return ms


if cmd == "estimate":
    ms = load()
    sample = random.Random(1).sample(ms, min(40, len(ms)))
    toks = chars = 0
    for m in sample:
        p = params(m)
        r = client.messages.count_tokens(model=MODEL, system=p["system"], messages=p["messages"])
        toks += r.input_tokens
        chars += len(user_msg(m))
    per_char = (toks - len(sample) * 0) / max(chars, 1)
    total_chars = sum(len(user_msg(m)) for m in ms)
    est_in = toks / len(sample) * len(ms)
    est_out = 60 * len(ms)
    print(json.dumps({"mentions": len(ms), "avg_input_tokens": round(toks / len(sample)), "est_input_tokens": int(est_in),
                      "est_output_tokens": est_out, "est_usd": round(est_in * PRICE_IN + est_out * PRICE_OUT, 2)}))

elif cmd == "submit":
    ms = load()
    done = set()
    lp = os.path.join(work, "labels.jsonl")
    if os.path.exists(lp):
        done = {json.loads(l)["cid"] for l in open(lp)}
    todo = [m for m in ms if m["cid"] not in done]
    ids = []
    for i in range(0, len(todo), 20000):
        chunk = todo[i:i + 20000]
        b = client.messages.batches.create(requests=[{"custom_id": m["cid"], "params": params(m)} for m in chunk])
        ids.append(b.id)
        print("submitted", b.id, len(chunk), flush=True)
    json.dump(ids, open(os.path.join(work, "label_batches.json"), "w"))

elif cmd == "collect":
    ids = json.load(open(os.path.join(work, "label_batches.json")))
    while True:
        st = [client.messages.batches.retrieve(i) for i in ids]
        print(time.strftime("%H:%M:%S"), [(s.processing_status, s.request_counts.succeeded, s.request_counts.processing) for s in st], flush=True)
        if all(s.processing_status == "ended" for s in st):
            break
        time.sleep(60)
    tin = tout = 0
    n_ok = n_bad = 0
    with open(os.path.join(work, "labels.jsonl"), "a", encoding="utf-8") as out:
        for i in ids:
            for r in client.messages.batches.results(i):
                if r.result.type != "succeeded":
                    n_bad += 1
                    continue
                msg = r.result.message
                u = msg.usage
                tin += u.input_tokens + (u.cache_creation_input_tokens or 0) + (u.cache_read_input_tokens or 0)
                tout += u.output_tokens
                txt = next((b.text for b in msg.content if b.type == "text"), "")
                try:
                    lab = json.loads(txt)
                except Exception:
                    n_bad += 1
                    continue
                n_ok += 1
                out.write(json.dumps({"cid": r.custom_id, **lab}, ensure_ascii=False) + "\n")
    usd = tin * PRICE_IN + tout * PRICE_OUT
    sp = os.path.join(work, "spend.json")
    spend = json.load(open(sp)) if os.path.exists(sp) else {}
    spend.setdefault("label", []).append({"model": MODEL, "batches": ids, "ok": n_ok, "failed": n_bad,
                                          "input_tokens": tin, "output_tokens": tout, "usd": round(usd, 4)})
    json.dump(spend, open(sp, "w"), indent=1)
    print("labels ok", n_ok, "failed", n_bad, "tokens", tin, tout, "usd", round(usd, 4))
