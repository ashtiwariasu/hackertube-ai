"""HN archive -> comments linking YouTube, with story title. Writes ~/hn/hn_yt.jsonl and prints rule tiers."""
import duckdb, json, re, time, collections
t0 = time.time(); con = duckdb.connect(); con.execute("SET threads TO 8")
con.execute("CREATE VIEW items AS SELECT * FROM read_parquet('/home/ubuntu/hn/parquet/**/*.parquet', union_by_name=true)")
con.execute("""CREATE TABLE yt AS SELECT id, "by" AS author, time, text, parent FROM items
               WHERE type = 2 AND deleted IS DISTINCT FROM 1 AND dead IS DISTINCT FROM 1 AND (text LIKE '%youtube.com%' OR text LIKE '%youtu.be%')""")
n = con.execute("SELECT count(*) FROM yt").fetchone()[0]; print("comments with youtube links:", n, f"({time.time()-t0:.0f}s)")
# resolve to the root story: walk parents up to 12 levels
con.execute("CREATE TABLE cur AS SELECT id AS cid, parent AS p FROM yt")
con.execute("CREATE TABLE done (cid UINTEGER, story UINTEGER)")
for i in range(12):
    con.execute("""INSERT INTO done SELECT c.cid, it.id FROM cur c JOIN items it ON it.id = c.p WHERE it.type = 1""")
    con.execute("""CREATE TABLE nxt AS SELECT c.cid, it.parent AS p FROM cur c JOIN items it ON it.id = c.p WHERE it.type = 2""")
    con.execute("DROP TABLE cur"); con.execute("ALTER TABLE nxt RENAME TO cur")
    left = con.execute("SELECT count(*) FROM cur").fetchone()[0]
    if left == 0: break
print("resolved stories:", con.execute("SELECT count(*) FROM done").fetchone()[0], f"({time.time()-t0:.0f}s)")
rows = con.execute("""SELECT y.id, y.author, strftime(y.time, '%Y-%m') AS month, y.text, s.title AS story_title, d.story
                      FROM yt y LEFT JOIN done d ON d.cid = y.id LEFT JOIN items s ON s.id = d.story""").fetchall()
VID = re.compile(r'(?:youtube(?:-nocookie)?\.com/(?:watch\?(?:[^"&\s<]*&(?:amp;)?)*v=|embed/|shorts/|v/|live/)|youtu\.be/)([A-Za-z0-9_-]{11})')
import html
by_vid = collections.defaultdict(set); authors = collections.defaultdict(set); out = open("/home/ubuntu/hn/hn_yt.jsonl", "w"); k = 0
for cid, author, month, text, title, story in rows:
    t = html.unescape(html.unescape(text or ""))
    vids = sorted(set(VID.findall(t)))
    if not vids: continue
    for v in vids:
        by_vid[v].add(month); authors[v].add(author)
    out.write(json.dumps({"id": cid, "author": author, "month": month, "story_title": title, "story": story, "vids": vids, "text": re.sub(r"<[^>]+>", " ", t)[:4000]}) + "\n"); k += 1
out.close()
print("comments with parsed video ids:", k, "distinct videos:", len(by_vid))
tiers = {}
for name, cond in [("3 people, 3 months (original)", lambda v: len(authors[v]) >= 3 and len(by_vid[v]) >= 3), ("2 people, 2 months", lambda v: len(authors[v]) >= 2 and len(by_vid[v]) >= 2), ("2 people, any", lambda v: len(authors[v]) >= 2), ("1 person", lambda v: True)]:
    tiers[name] = sum(1 for v in by_vid if cond(v))
for name, c in tiers.items(): print(f"  {name}: {c}")
print(f"done in {time.time()-t0:.0f}s")
