"""Shared labelling call: stance + why for one comment that links a video. Mirrors the HackerTube dataset labels.
Static instructions first so vLLM prefix caching reuses them; the comment comes last."""
import json, urllib.request
URL = "http://127.0.0.1:8100/v1/chat/completions"
SCHEMA = {"type": "object", "properties": {"stance": {"type": "string", "enum": ["strong_rec", "rec", "reference", "joke", "critical"]},
          "why": {"type": "string"}, "quote": {"type": "string"}}, "required": ["stance", "why", "quote"], "additionalProperties": False}
SYSTEM = """You classify Hacker News comments that link a YouTube video. Decide what the commenter is doing with the link and say why someone would watch the video.

Labels:
strong_rec: explicitly urges others to watch it, calls it great, best, must-see, changed how they think.
rec: recommends or suggests it, points people to it as worth watching.
reference: cites it as evidence or illustration for a point; not primarily a recommendation.
joke: the link is a joke, meme or punchline.
critical: links it to criticise, debunk or mock the video itself.

Return JSON: stance (one label), why (one sentence, at most 25 words, stating what the video shows or argues and why the commenter brought it up; no hype), quote (the commenter's own words about the video, at most 15 words, copied exactly, or empty)."""
USER = """Story title: {story}
Comment (the link is shown as [VIDEO]):
{text}"""

def label(story, text, model="labeller", timeout=180):
    body = {"model": model, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER.format(story=story or "", text=(text or "")[:3000])}],
            "max_tokens": 220, "temperature": 0, "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_schema", "json_schema": {"name": "label", "schema": SCHEMA}}}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.load(r)
    return json.loads(out["choices"][0]["message"]["content"]), out.get("usage", {})
