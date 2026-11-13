"""Read a YouTube screenshot with Claude, match its titles to the catalogue, build an instant profile.
The image is sent to the model once and never stored."""
import base64, io, json, logging, os, re

import anthropic
from PIL import Image, UnidentifiedImageError

log = logging.getLogger("hackertube.screenshot")
MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
FALLBACK = os.environ.get("CLAUDE_FALLBACK", "claude-opus-4-8")
MAX_SIDE = 1568

PROMPT = """These are screenshots from someone's YouTube: watch history, liked videos, subscriptions, a playlist, or the home feed.
List every video and every channel name you can read. Copy titles exactly as shown; if a title is cut off, keep the visible part and end it with "...". Leave channel as "" when it isn't visible. Do not invent entries. Ignore ads, Shorts shelves, and interface labels.
Return only JSON of this shape:
{"page": "history" | "liked" | "subscriptions" | "playlist" | "home" | "other", "videos": [{"title": "...", "channel": "..."}], "channels": ["..."]}"""


class ScreenshotError(Exception):
    """Safe to show to the user."""


def prepare(raw: bytes) -> str:
    try:
        im = Image.open(io.BytesIO(raw))
        im.load()
    except (UnidentifiedImageError, OSError):
        raise ScreenshotError("That file isn't an image we can read. Use a PNG or JPG screenshot.")
    im = im.convert("RGB")
    w, h = im.size
    scale = min(1.0, MAX_SIDE / max(w, h))
    if scale < 1:
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85, optimize=True)  # re-encoding also drops metadata
    return base64.b64encode(buf.getvalue()).decode()


def extract(images):
    client = anthropic.Anthropic()
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": prepare(r)}}
               for r in images]
    content.append({"type": "text", "text": PROMPT})
    kwargs = dict(model=MODEL, max_tokens=4000, output_config={"effort": "low"},
                  messages=[{"role": "user", "content": content}])
    try:
        resp = client.beta.messages.create(betas=["server-side-fallback-2026-06-01"],
                                           fallbacks=[{"model": FALLBACK}], **kwargs)
    except TypeError:
        resp = client.messages.create(**kwargs)
    if resp.stop_reason == "refusal":
        raise ScreenshotError("The reader declined that image. Try a plain screenshot of your YouTube page.")
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", "") == "text")
    m = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(m.group(0)) if m else {}
    except json.JSONDecodeError:
        data = {}
    videos = []
    for v in data.get("videos") or []:
        if isinstance(v, dict) and str(v.get("title") or "").strip():
            videos.append({"title": str(v["title"])[:200].strip(), "channel": str(v.get("channel") or "")[:100].strip()})
    channels = [str(c)[:100].strip() for c in (data.get("channels") or []) if str(c).strip()]
    log.info("screenshot read model=%s videos=%d channels=%d tokens=%d/%d", resp.model, len(videos), len(channels),
             resp.usage.input_tokens, resp.usage.output_tokens)
    return {"page": str(data.get("page") or "other"), "videos": videos[:200], "channels": channels[:100]}


def merge_extractions(parts):
    """Combine per-image results: dedupe titles and channels, keep the most specific page type."""
    seen, videos, channels, page = set(), [], [], "other"
    for part in parts:
        for v in part["videos"]:
            k = re.sub(r"[^a-z0-9]+", " ", v["title"].lower()).strip()
            if k and k not in seen:
                seen.add(k)
                videos.append(v)
        for c in part["channels"]:
            if c not in channels:
                channels.append(c)
        if part["page"] in ("history", "liked", "subscriptions", "playlist") and page == "other":
            page = part["page"]
    return {"page": page, "videos": videos[:200], "channels": channels[:100]}


def analyse(cat, extracted):
    import numpy as np
    matched, unmatched = cat.match_titles(extracted["videos"])
    texts = [f"{v['title']} {v['channel']}".strip() for v in extracted["videos"]]
    texts += [f"YouTube channel: {c}" for c in extracted["channels"]]
    vec = cat.combine([(2.0, cat.vids_vector(matched)), (1.0, cat.text_vector(texts))]) if texts else None
    interests = cat.interests(vec) if vec else []
    top10 = float(np.sort(cat.emb @ np.asarray(vec, dtype=np.float32))[-10:].mean()) if vec else 0.0
    # how much of this history lives in HN's world: catalogue matches, else closeness to the catalogue
    confidence = "strong" if len(matched) >= 2 else ("ok" if (matched or top10 >= 0.745) else "weak")
    if vec and confidence != "weak":
        picks = cat.personal(vec, exclude=set(matched), n=12)
    elif vec:
        near = cat.personal(vec, exclude=set(matched), n=6)
        picks = near + [v for v in cat.daily_mix(cat.ranked[:120], 8, "shot") if v not in near][:6]
    else:
        picks = cat.daily_mix(cat.ranked[:120], 12, "shot")
    return {"page": extracted["page"], "recognised": len(extracted["videos"]), "channels": extracted["channels"],
            "matched": [cat.by_vid[v] for v in matched], "unmatched": unmatched, "interests": interests,
            "picks": picks, "essentials_seen": len(set(matched) & set(cat.essentials)),
            "ess_total": len(cat.essentials), "vids": matched, "titles": texts[:300],
            "confidence": confidence, "closeness": round(top10, 3)}
