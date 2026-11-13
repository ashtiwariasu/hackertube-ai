#!/usr/bin/env python3
"""Export candidate stats + a ready-to-label mentions file from the Bluesky collector db.

No API calls, no money. Run any time (reads the live db read-only, WAL-safe):
  .venv/bin/python export_pack.py [--db data/bluesky.db] [--out out]

Writes:
  out/candidates.jsonl         per video with >= --cand-min-authors distinct authors (all mentions):
                               mentions, authors, months, and the same counted only over mentions with
                               >= --min-chars of the author's own text (links stripped) -> q_*;
                               meets_rule = q_authors >= 3 and q_months >= 3 (the PACKS.md inclusion rule)
  out/mentions_to_label.jsonl  for videos with >= --label-min-authors distinct q_authors: qualifying
                               mentions in the PACKS.md mention shape minus stance/why/quote,
                               up to --per-vid per video, one per author first (longest text first)
  out/export_summary.json      counts at each stage
"""
import argparse, json, os, re, sqlite3, sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ytlinks import vid_from_url, TEXT_URL_RE  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ANY_URL_RE = re.compile(r'(?:https?://\S+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}/\S*)', re.I)
TOKEN_RE = re.compile(r'\[(?:VIDEO|LINK)\]')
NOT_OWN_RE = re.compile(r'(?:\bvia @?youtube\b|[#@][\w.:-]+)', re.I)


def _norm(s):
    return re.sub(r'\s+', ' ', re.sub(r'[^\w\s]', ' ', s.lower())).strip()


def clean_text(text, facet_links, vid, title=''):
    """Replace links with [VIDEO] (this video) or [LINK]; return (clean, own_chars).
    own_chars excludes link tokens, hashtags, @mentions, 'via @YouTube' and a pasted video title,
    so auto-shares ("<title> via @YouTube #tags") do not count as the author's own words."""
    b = (text or '').encode('utf-8')
    spans = []
    for s, e, uri in facet_links or []:
        if isinstance(s, int) and isinstance(e, int) and 0 <= s < e <= len(b):
            spans.append((s, e, b'[VIDEO]' if vid_from_url(uri) == vid else b'[LINK]'))
    for s, e, tok in sorted(spans, reverse=True):
        b = b[:s] + tok + b[e:]
    t = b.decode('utf-8', 'replace')
    t = TEXT_URL_RE.sub(lambda m: '[VIDEO]' if vid_from_url(m.group(0)) == vid else '[LINK]', t)
    t = ANY_URL_RE.sub('[LINK]', t)
    own = _norm(NOT_OWN_RE.sub(' ', TOKEN_RE.sub(' ', t)))
    tn = _norm(title or '')
    if len(tn) >= 15:
        own = own.replace(tn, ' ')
        own = own.replace(tn[:max(15, len(tn) * 2 // 3)], ' ')  # titles are often pasted cut short
    own = re.sub(r'\s+', ' ', own).strip()
    return t[:1500], len(own)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', default=os.path.join(HERE, 'data', 'bluesky.db'))
    ap.add_argument('--out', default=os.path.join(HERE, 'out'))
    ap.add_argument('--min-chars', type=int, default=100)
    ap.add_argument('--cand-min-authors', type=int, default=2)
    ap.add_argument('--label-min-authors', type=int, default=3)
    ap.add_argument('--per-vid', type=int, default=40, help='label more than the final 20 so the pack can favour recs')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    db = sqlite3.connect(f'file:{a.db}?mode=ro', uri=True, timeout=30)
    inactive = {r[0] for r in db.execute('SELECT did FROM accounts WHERE active=0')}
    rows = db.execute('''SELECT pv.vid, p.uri, p.did, p.rkey, p.month, p.text, p.langs, p.facet_links, p.embed,
                                p.reply_root, p.src
                         FROM post_videos pv JOIN posts p ON p.uri = pv.uri''').fetchall()
    summ = dict(posts=db.execute('SELECT COUNT(*) FROM posts').fetchone()[0], post_video_rows=len(rows),
                skipped_inactive_accounts=0)
    per = defaultdict(list)
    for vid, uri, did, rkey, month, text, langs, fl, emb, root, src in rows:
        if did in inactive:
            summ['skipped_inactive_accounts'] += 1
            continue
        e = json.loads(emb) if emb else {}
        clean, own = clean_text(text, json.loads(fl) if fl else [], vid, e.get('title') or '')
        per[vid].append(dict(id=uri, url=f'https://bsky.app/profile/{did}/post/{rkey}', vid=vid, community='bluesky',
                             author=did, month=month, context=(e.get('title') or '') if vid_from_url(e.get('uri')) == vid else '',
                             text=clean, own_chars=own, langs=json.loads(langs or '[]'), is_reply=bool(root), src=src))
    summ['videos'] = len(per)
    cands, label, stage = [], [], defaultdict(int)
    for vid, ms in per.items():
        q = [m for m in ms if m['own_chars'] >= a.min_chars]
        st = dict(vid=vid, mentions=len(ms), authors=len({m['author'] for m in ms}),
                  months=len({m['month'] for m in ms}),
                  q_mentions=len(q), q_authors=len({m['author'] for m in q}), q_months=len({m['month'] for m in q}),
                  first_month=min(m['month'] for m in ms), last_month=max(m['month'] for m in ms),
                  title_hint=next((m['context'] for m in ms if m['context']), ''))
        st['meets_rule'] = st['q_authors'] >= 3 and st['q_months'] >= 3
        for k, ok in (('authors>=2', st['authors'] >= 2), ('authors>=3', st['authors'] >= 3),
                      ('q_authors>=3', st['q_authors'] >= 3), ('q_authors>=3&q_months>=2', st['q_authors'] >= 3 and st['q_months'] >= 2),
                      ('meets_rule', st['meets_rule'])):
            stage[k] += ok
        if st['authors'] >= a.cand_min_authors:
            cands.append(st)
        if st['q_authors'] >= a.label_min_authors:
            byauth = defaultdict(list)
            for m in sorted(q, key=lambda m: -m['own_chars']):
                byauth[m['author']].append(m)
            pick, rnd = [], 0
            while len(pick) < a.per_vid and any(len(v) > rnd for v in byauth.values()):
                pick += [v[rnd] for v in byauth.values() if len(v) > rnd][:a.per_vid - len(pick)]
                rnd += 1
            label += pick
    cands.sort(key=lambda s: (-s['q_authors'], -s['authors'], -s['mentions']))
    with open(os.path.join(a.out, 'candidates.jsonl'), 'w') as f:
        for s in cands:
            f.write(json.dumps(s, ensure_ascii=False) + '\n')
    with open(os.path.join(a.out, 'mentions_to_label.jsonl'), 'w') as f:
        for m in label:
            f.write(json.dumps(m, ensure_ascii=False) + '\n')
    summ.update(stages=dict(stage), candidates_written=len(cands), label_mentions=len(label),
                label_videos=len({m['vid'] for m in label}),
                months_in_db=[r[0] for r in db.execute('SELECT DISTINCT month FROM posts ORDER BY 1')])
    with open(os.path.join(a.out, 'export_summary.json'), 'w') as f:
        json.dump(summ, f, indent=1)
    print(json.dumps(summ, indent=1))


if __name__ == '__main__':
    main()
