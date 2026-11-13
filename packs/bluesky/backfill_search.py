#!/usr/bin/env python3
"""One-off, polite backfill from the public Bluesky search API (no auth).

Limits found 3-Nov-2026: unauthenticated `cursor` paging returns 403 ("Request forbidden by
administrative rules"), so each query yields at most one page (100 posts). We therefore take
one page per fixed time window (since/until) per domain: a uniform SAMPLE, not a full history.
We deliberately do not walk `until` backwards to emulate paging.
"""
import argparse, json, os, sqlite3, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ytlinks import extract  # noqa: E402
from collector import SCHEMA, DB, now_iso  # noqa: E402

API = 'https://api.bsky.app/xrpc/app.bsky.feed.searchPosts'
UA = 'hackertube.ai research collector (contact via hackertube.ai)'


def fetch(params, tries=5):
    url = API + '?' + urllib.parse.urlencode(params)
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r), 200
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                wait = int(e.headers.get('Retry-After') or 0) or 5 * 2 ** i
                print(f'  http {e.code}, sleeping {wait}s', flush=True)
                time.sleep(wait)
                continue
            return None, e.code
        except Exception as e:  # network blip
            print('  error', repr(e), flush=True)
            time.sleep(5 * 2 ** i)
    return None, -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=21)
    ap.add_argument('--window-min', type=int, default=60)
    ap.add_argument('--sleep', type=float, default=1.0)
    a = ap.parse_args()
    db = sqlite3.connect(DB, isolation_level=None, timeout=60)
    db.executescript(SCHEMA)
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=a.days)
    tot = dict(requests=0, saturated=0, posts=0, yt_posts_new=0, errors=0, codes={})
    for dom in ('youtube.com', 'youtu.be'):
        t = end
        while t > start:
            since = t - timedelta(minutes=a.window_min)
            params = dict(q=dom, domain=dom, sort='latest', limit=100,
                          since=since.strftime('%Y-%m-%dT%H:%M:%SZ'), until=t.strftime('%Y-%m-%dT%H:%M:%SZ'))
            d, code = fetch(params)
            tot['requests'] += 1
            tot['codes'][code] = tot['codes'].get(code, 0) + 1
            posts = (d or {}).get('posts') or []
            if d is None:
                tot['errors'] += 1
            if len(posts) >= 100:
                tot['saturated'] += 1
            rows, links = [], []
            for p in posts:
                rec = p.get('record') or {}
                vids, fl, emb = extract(rec)
                if not vids:
                    continue
                uri, did = p['uri'], p['author']['did']
                seen = p.get('indexedAt') or rec.get('createdAt') or ''
                reply = rec.get('reply') or {}
                rows.append((uri, did, uri.rsplit('/', 1)[-1], p.get('cid'), (rec.get('createdAt') or '')[:40],
                             seen[:32], seen[:7], (rec.get('text') or '')[:1500], json.dumps(rec.get('langs') or []),
                             json.dumps(fl) if fl else None, json.dumps(emb) if emb else None,
                             (reply.get('root') or {}).get('uri'), (reply.get('parent') or {}).get('uri'),
                             'search', None, now_iso()))
                links += [(uri, v) for v in vids]
            tot['posts'] += len(posts)
            if rows:
                db.execute('BEGIN IMMEDIATE')
                before = db.total_changes
                db.executemany('INSERT OR IGNORE INTO posts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)
                tot['yt_posts_new'] += db.total_changes - before
                db.executemany('INSERT OR IGNORE INTO post_videos VALUES(?,?)', links)
                db.execute('COMMIT')
            if tot['requests'] % 50 == 0:
                print(dom, since.isoformat(), json.dumps(tot), flush=True)
            t = since
            time.sleep(a.sleep)
    db.execute('INSERT OR REPLACE INTO state(k,v) VALUES(?,?)', ('search_backfill', json.dumps(
        dict(done_at=now_iso(), days=a.days, window_min=a.window_min, **tot))))
    print('DONE', json.dumps(tot), flush=True)


if __name__ == '__main__':
    main()
