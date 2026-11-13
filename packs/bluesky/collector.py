#!/usr/bin/env python3
"""Bluesky Jetstream (v2) collector: keeps posts that link YouTube videos.

- live tail of app.bsky.feed.post commits + account events (no auth, unmetered)
- stores matching posts in SQLite (WAL); honours post deletes and account deletion/takedown
- persists the Jetstream cursor (seq + time_us) in the same transaction as the data,
  so a restart resumes without gaps inside Jetstream's lookback window (~36 h observed)
"""
import asyncio, json, logging, logging.handlers, os, signal, sqlite3, sys, time
from datetime import datetime, timezone

import websockets

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ytlinks import extract, quick_maybe_yt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get('BSKY_DB', os.path.join(HERE, 'data', 'bluesky.db'))
LOG = os.path.join(HERE, 'logs', 'collector.log')
STATUS = os.path.join(HERE, 'data', 'status.json')
HOSTS = ['jetstream.us-east.bsky.network', 'jetstream.us-west.bsky.network']  # Jetstream v2 public
PATH = '/xrpc/network.bsky.jetstream.subscribeEvents?collections=app.bsky.feed.post&kinds=commit&kinds=account'
FIRST_START_LOOKBACK_H = float(os.environ.get('BSKY_FIRST_LOOKBACK_H', '48'))  # clamped by server to its floor
COMMIT_EVERY_S = 1.0
TEXT_MAX = 1500

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS posts(
  uri TEXT PRIMARY KEY, did TEXT NOT NULL, rkey TEXT, cid TEXT,
  created_at TEXT, seen_at TEXT, month TEXT, text TEXT, langs TEXT,
  facet_links TEXT, embed TEXT, reply_root TEXT, reply_parent TEXT,
  src TEXT, seq INTEGER, inserted_at TEXT);
CREATE INDEX IF NOT EXISTS posts_did ON posts(did);
CREATE TABLE IF NOT EXISTS post_videos(uri TEXT NOT NULL, vid TEXT NOT NULL, PRIMARY KEY(uri, vid));
CREATE INDEX IF NOT EXISTS pv_vid ON post_videos(vid);
CREATE TABLE IF NOT EXISTS accounts(did TEXT PRIMARY KEY, active INTEGER, status TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS state(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS gaps(at TEXT, host TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS deletions(at TEXT, kind TEXT, n INTEGER);
"""

log = logging.getLogger('bsky')


def setup_logging():
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    h = logging.handlers.RotatingFileHandler(LOG, maxBytes=5_000_000, backupCount=3)
    h.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    log.addHandler(h)
    s = logging.StreamHandler(sys.stdout)
    s.setFormatter(logging.Formatter('%(levelname)s %(message)s'))
    log.addHandler(s)
    log.setLevel(logging.INFO)


def now_iso():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def iso_to_us(t):
    try:
        return int(datetime.fromisoformat(t.replace('Z', '+00:00')).timestamp() * 1e6)
    except Exception:
        return None


class Store:
    def __init__(self, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None, timeout=30)
        self.db.executescript(SCHEMA)
        self.db.execute('PRAGMA synchronous=NORMAL')
        self.ops = []  # buffered (fn, args); applied once a second in one short write transaction

    def get(self, k, default=None):
        r = self.db.execute('SELECT v FROM state WHERE k=?', (k,)).fetchone()
        return r[0] if r else default

    def put(self, k, v):
        self.db.execute('INSERT OR REPLACE INTO state(k,v) VALUES(?,?)', (k, str(v)))

    def flush(self, state):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            for fn, args in self.ops:
                fn(*args)
            for k, v in state.items():
                self.put(k, v)
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise
        self.ops = []

    def close(self):
        self.db.close()


class Collector:
    def __init__(self):
        self.st = Store(DB)
        self.stop = False
        self.c = dict(frames=0, creates=0, updates=0, deletes=0, accounts=0, yt_posts=0, yt_deleted=0,
                      acct_purged=0, info=0, errors=0)
        self.start = time.time()
        self.last_seq = int(self.st.get('last_seq', 0) or 0)
        self.last_time_us = int(self.st.get('last_time_us', 0) or 0)
        self.host = self.st.get('host', HOSTS[0])
        self.last_event_time = None
        self.win = []  # (t, frames) for recent rate

    # ---------- event handling ----------
    def handle(self, raw):
        self.c['frames'] += 1
        ev = json.loads(raw)
        p = ev.get('payload') or {}
        t = p.get('$type', '')
        seq = p.get('seq')
        if t.endswith('#commit'):
            op = p.get('operation')
            if p.get('collection') == 'app.bsky.feed.post':
                uri = f"at://{p.get('did')}/app.bsky.feed.post/{p.get('rkey')}"
                if op == 'delete':
                    self.c['deletes'] += 1
                    self.st.ops.append((self.delete_post, (uri,)))
                elif op in ('create', 'update'):
                    self.c['creates' if op == 'create' else 'updates'] += 1
                    if quick_maybe_yt(raw):
                        self.st.ops.append((self.save_post, (uri, p, seq)))
                    elif op == 'update':
                        self.st.ops.append((self.delete_post, (uri,)))  # edited away from YouTube
        elif t.endswith('#account'):
            self.c['accounts'] += 1
            self.st.ops.append((self.account, (p.get('account') or {}, p.get('did'))))
        elif t.endswith('#info'):
            self.c['info'] += 1
            log.warning('info from server: %s', p)
            if p.get('name') == 'OutdatedCursor':
                self.st.ops.append((self.gap, (self.host, json.dumps(p)[:500])))
        elif t.endswith('#error') or ev.get('$type') == 'error':
            self.c['errors'] += 1
            log.error('error frame: %s', raw[:500])
        if isinstance(seq, int):
            self.last_seq = seq
            tu = iso_to_us(p.get('time') or '')
            if tu:
                self.last_time_us = tu
                self.last_event_time = tu / 1e6

    def gap(self, host, detail):
        self.st.db.execute('INSERT INTO gaps VALUES(?,?,?)', (now_iso(), host, detail))

    def save_post(self, uri, p, seq):
        rec = p.get('record') or {}
        vids, links, emb = extract(rec)
        if not vids:
            if p.get('operation') == 'update':
                self.delete_post(uri)
            return
        seen = p.get('witnessedAt') or p.get('time') or now_iso()
        reply = rec.get('reply') or {}
        db = self.st.db
        existed = db.execute('SELECT 1 FROM posts WHERE uri=?', (uri,)).fetchone()
        db.execute('INSERT OR REPLACE INTO posts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (
            uri, p.get('did'), p.get('rkey'), p.get('cid'), (rec.get('createdAt') or '')[:40], seen[:32], seen[:7],
            (rec.get('text') or '')[:TEXT_MAX], json.dumps(rec.get('langs') or []),
            json.dumps(links) if links else None, json.dumps(emb) if emb else None,
            (reply.get('root') or {}).get('uri'), (reply.get('parent') or {}).get('uri'),
            'jetstream', seq, now_iso()))
        db.execute('DELETE FROM post_videos WHERE uri=?', (uri,))
        db.executemany('INSERT OR IGNORE INTO post_videos VALUES(?,?)', [(uri, v) for v in vids])
        if not existed:
            self.c['yt_posts'] += 1

    def delete_post(self, uri):
        cur = self.st.db.execute('DELETE FROM posts WHERE uri=?', (uri,))
        if cur.rowcount:
            self.st.db.execute('DELETE FROM post_videos WHERE uri=?', (uri,))
            self.st.db.execute('INSERT INTO deletions VALUES(?,?,?)', (now_iso(), 'post', 1))
            self.c['yt_deleted'] += 1

    def account(self, a, did):
        did = a.get('did') or did
        if not did:
            return
        active = bool(a.get('active'))
        status = a.get('status') or ('active' if active else 'inactive')
        db = self.st.db
        db.execute('INSERT OR REPLACE INTO accounts VALUES(?,?,?,?)', (did, int(active), status, now_iso()))
        if not active and status in ('deleted', 'takendown'):
            uris = [r[0] for r in db.execute('SELECT uri FROM posts WHERE did=?', (did,))]
            if uris:
                db.executemany('DELETE FROM post_videos WHERE uri=?', [(u,) for u in uris])
                db.execute('DELETE FROM posts WHERE did=?', (did,))
                db.execute('INSERT INTO deletions VALUES(?,?,?)', (now_iso(), 'account_' + status, len(uris)))
                self.c['acct_purged'] += len(uris)

    def checkpoint(self):
        self.st.flush({'last_seq': self.last_seq, 'last_time_us': self.last_time_us, 'host': self.host})

    # ---------- connection ----------
    def url_for(self, host, mode):
        base = f'wss://{host}{PATH}'
        if mode == 'seq' and self.last_seq:
            return base + f'&cursor={self.last_seq}'
        if self.last_time_us:
            return base + f'&cursor={self.last_time_us - 10_000_000}'
        return base + f'&cursor={int((time.time() - FIRST_START_LOOKBACK_H * 3600) * 1e6)}'

    async def run(self):
        backoff, hi, mode = 1, HOSTS.index(self.host) if self.host in HOSTS else 0, 'seq'
        stat_task = asyncio.create_task(self.status_loop())
        while not self.stop:
            host = HOSTS[hi]
            if host != self.host:
                mode = 'time'  # seq numbers are per instance; resume another host by timestamp
            url = self.url_for(host, mode)
            log.info('connecting %s', url)
            try:
                async with websockets.connect(url, subprotocols=['xrpc.v1.json'], max_size=2 ** 23,
                                              open_timeout=30, ping_interval=30, ping_timeout=60,
                                              close_timeout=5) as ws:
                    self.host, backoff, mode = host, 1, 'seq'
                    last_commit = time.time()
                    while not self.stop:
                        raw = await asyncio.wait_for(ws.recv(), timeout=120)
                        try:
                            self.handle(raw)
                        except (ValueError, KeyError, TypeError) as e:
                            self.c['errors'] += 1
                            log.warning('bad frame %r: %s', e, raw[:200])
                        if time.time() - last_commit >= COMMIT_EVERY_S:
                            self.checkpoint()
                            last_commit = time.time()
            except websockets.exceptions.InvalidStatus as e:
                code = e.response.status_code
                body = (e.response.body or b'')[:300]
                log.warning('handshake %s on %s: %s', code, host, body)
                if code == 400 and b'CursorTooOld' in body:
                    self.st.ops.append((self.gap, (host, body.decode('utf8', 'replace'))))
                    mode = 'time'  # timestamp cursors are clamped to the retention floor
                    continue
                hi = (hi + 1) % len(HOSTS)
            except (asyncio.TimeoutError, OSError, websockets.exceptions.WebSocketException) as e:
                log.warning('connection lost on %s: %r', host, e)
                if backoff >= 16:
                    hi = (hi + 1) % len(HOSTS)
            except Exception as e:  # library edge cases (e.g. SSL transport gone mid-recv): reconnect
                log.exception('unexpected error on %s: %r', host, e)
            finally:
                try:
                    self.checkpoint()
                except sqlite3.Error as e:
                    log.error('checkpoint failed: %r', e)
            if not self.stop:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
        stat_task.cancel()
        self.checkpoint()
        self.write_status()
        self.st.close()
        log.info('stopped cleanly at seq %s', self.last_seq)

    def write_status(self):
        now = time.time()
        self.win.append((now, self.c['frames']))
        self.win = [w for w in self.win if now - w[0] <= 300]
        rate = (self.win[-1][1] - self.win[0][1]) / max(1, self.win[-1][0] - self.win[0][0])
        db = self.st.db
        s = dict(now=now_iso(), uptime_s=int(now - self.start), host=self.host, last_seq=self.last_seq,
                 lag_s=round(now - self.last_event_time, 1) if self.last_event_time else None,
                 frames_per_s_5min=round(rate, 1), counters=self.c,
                 db_posts=db.execute('SELECT COUNT(*) FROM posts').fetchone()[0],
                 db_videos=db.execute('SELECT COUNT(DISTINCT vid) FROM post_videos').fetchone()[0],
                 db_bytes=sum(os.path.getsize(DB + x) for x in ('', '-wal') if os.path.exists(DB + x)))
        os.makedirs(os.path.dirname(STATUS), exist_ok=True)
        tmp = STATUS + '.tmp'
        with open(tmp, 'w') as f:
            json.dump(s, f, indent=1)
        os.replace(tmp, STATUS)
        return s

    async def status_loop(self):
        while True:
            await asyncio.sleep(60)
            try:
                s = self.write_status()
                log.info('status lag=%ss rate=%s/s posts=%s vids=%s c=%s', s['lag_s'], s['frames_per_s_5min'],
                         s['db_posts'], s['db_videos'], json.dumps(self.c))
            except Exception as e:  # never let status kill the collector
                log.error('status failed: %r', e)


def main():
    setup_logging()
    col = Collector()
    loop = asyncio.new_event_loop()

    def _stop(*_):
        col.stop = True  # frames arrive many times a second, so the recv loop exits promptly
        log.info('stop requested')
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _stop)
    log.info('starting; db=%s last_seq=%s host=%s', DB, col.last_seq, col.host)
    loop.run_until_complete(col.run())


if __name__ == '__main__':
    main()
