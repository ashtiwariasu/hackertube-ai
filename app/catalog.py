"""In-memory catalogue: videos, mentions, topics, embeddings, feeds and search."""
import collections, datetime, json, os, random, re, threading, zlib
import numpy as np

from .web_source import WEB_COMM, WEB_LABEL, WEB_SHORT, web_only

DATA = os.path.join(os.path.dirname(__file__), "data")
# Web-only videos (open-web source, no HN or SE community) are in the catalogue, search and the personal feed,
# but stay out of the home rows and the ranked lists unless HT_WEB_ONLY_ROWS=1 (or Catalog(web_only_rows=True)),
# or the visitor filters the home feed by "The open web" itself.
WEB_ONLY_ROWS = os.environ.get("HT_WEB_ONLY_ROWS", "").strip().lower() in ("1", "true", "yes", "on")

# The personal shortlist (Pro's "For you"): see Catalog.librarian
LIB_FIRST = 12               # the first screen of it
LIB_FOLLOWED_AT = (0, 3, 7)  # where creators the person follows go on that screen: enough to recognise, room to discover
LIB_PER_INTEREST = 2         # picks one interest can take on the first screen (1 when the interest is a single video)
LIB_SHORT_CLIP = 240         # under 4 minutes is a clip, not a talk
LIB_ITEM_MAX = 4.0           # the most a match to one video can count, in standard deviations above the catalogue


class HalfMatrix:
    """Embeddings kept as float16 (half the memory of float32) that still behave like a float32 matrix for the
    operations the app uses: `m @ v`, `m @ M`, `q @ m.T`, `m[i]`, `m[rows]`. Products convert in chunks, so the
    temporary float32 copy stays small; at 100k videos a similarity query costs ~4 ms instead of ~2 ms."""
    __array_ufunc__ = None  # make `ndarray @ HalfMatrix` defer to __rmatmul__
    CHUNK = 16384

    def __init__(self, a):
        self.a = np.asarray(a, dtype=np.float16)
        self.shape, self.dtype = self.a.shape, np.dtype(np.float32)

    def __len__(self):
        return len(self.a)

    def __getitem__(self, idx):
        return self.a[idx].astype(np.float32)

    def __matmul__(self, x):
        x = np.asarray(x, dtype=np.float32)
        out = np.empty((len(self.a),) + x.shape[1:], dtype=np.float32)
        for i in range(0, len(self.a), self.CHUNK):
            out[i:i + self.CHUNK] = self.a[i:i + self.CHUNK].astype(np.float32) @ x
        return out

    @property
    def T(self):
        return _HalfT(self)

    @property
    def nbytes(self):
        return self.a.nbytes


class _HalfT:
    __array_ufunc__ = None

    def __init__(self, m):
        self.m = m

    def __rmatmul__(self, q):  # q @ m.T == (m @ q.T).T
        return (self.m @ np.asarray(q, dtype=np.float32).T).T


def norm_key(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split())


def jaccard(a, b):
    a, b = set(a), set(b)
    return len(a & b) / (len(a | b) or 1)


def slugify(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s.lower()).strip("-")
    return s[:80] or "video"


def fmt_dur(sec: int) -> str:
    if not sec:
        return ""
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_views(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n // 1000}K"
    return str(n)


def iso_dur(sec: int) -> str:
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"PT{h}H{m}M{s}S"


VIEWERS, VIEWERS_LABEL = "viewers", "HackerTube viewers"


def fix_viewers(v):
    """A video that HackerTube viewers vouched for: label it properly in its why list and its card badge."""
    c = (v.get("comm") or {}).get(VIEWERS)
    if not c:
        return
    for w in v.get("why_list") or []:
        if w.get("c") == VIEWERS:
            w["label"] = VIEWERS_LABEL
    if v.get("primary") == VIEWERS:
        n = c.get("recommenders", 0)
        v["badge"] = f"{n} viewer rec{'' if n == 1 else 's'}" if n else ""


# Videos people submitted that aren't in the release yet: vid -> record. Module level, so it survives a catalogue
# reload. They are found by get() but stay out of by_vid, rankings, topics, search and sitemaps, and they have no
# row in the embedding matrices.
ADDED = {}


class Catalog:
    def __init__(self, data_dir=None, web_only_rows=None):
        DATA = data_dir or globals()["DATA"]
        self.web_only_rows = WEB_ONLY_ROWS if web_only_rows is None else bool(web_only_rows)
        self.videos = json.load(open(f"{DATA}/videos.json", encoding="utf-8"))
        self.mentions = json.load(open(f"{DATA}/mentions.json", encoding="utf-8"))
        self.related = json.load(open(f"{DATA}/related.json", encoding="utf-8"))
        self.authors = json.load(open(f"{DATA}/authors.json", encoding="utf-8"))
        self.taxonomy = json.load(open(f"{DATA}/taxonomy.json", encoding="utf-8"))
        self.emb = HalfMatrix(np.load(f"{DATA}/embeddings.npy"))
        self.title_emb = HalfMatrix(np.load(f"{DATA}/title_emb.npy"))
        self.by_vid = {}
        for i, v in enumerate(self.videos):
            v["i"] = i
            v["slug"] = slugify(v["title"])
            v["url"] = f"/v/{v['vid']}/{v['slug']}"
            v["dur"], v["views"] = int(v.get("dur") or 0), int(v.get("views") or 0)
            v["published"] = v.get("published") or ""
            v["channel_id"] = v.get("channel_id") or "name:" + v["channel"]   # without YouTube's ids, group by name
            v["dur_s"] = fmt_dur(v["dur"])
            v["views_s"] = fmt_views(v["views"]) if v["views"] else ""
            v["year"] = v["published"][:4]
            self.by_vid[v["vid"]] = v
        self.cats = {}
        self.areas = []
        for a in self.taxonomy["areas"]:
            area = {"id": a["id"], "name": a["name"], "cats": []}
            for c in a["categories"]:
                cat = {"id": c["id"], "name": c["name"], "description": c.get("description", ""),
                       "area": a["name"], "area_id": a["id"], "vids": []}
                self.cats[c["id"]] = cat
                area["cats"].append(cat)
            self.areas.append(area)
        for v in self.videos:
            for cid in v["cats"]:
                if cid in self.cats:
                    self.cats[cid]["vids"].append(v["vid"])
        self._cat_overlap = {}
        for v in self.videos:
            for x in v["cats"]:
                for y in v["cats"]:
                    if x != y and x in self.cats and y in self.cats:
                        self._cat_overlap[(x, y)] = self._cat_overlap.get((x, y), 0) + 1
        for a in self.areas:
            a["cats"] = [c for c in a["cats"] if c["vids"]]
            a["cats"].sort(key=lambda c: -len(c["vids"]))
            a["count"] = len({vid for c in a["cats"] for vid in c["vids"]})
        self.areas.sort(key=lambda a: -a["count"])
        self.title_index, self.title_keys = {}, []
        for v in self.videos:
            k = norm_key(v["title"])
            self.title_index.setdefault(k, v["vid"])
            self.title_keys.append((k, v["vid"]))
        cpath = f"{DATA}/communities.json"
        meta = json.load(open(cpath)) if os.path.exists(cpath) else {}
        self.comms = {}
        # The open-web community is the curated pool (rows the pack marks curated) when the pack marks one;
        # every web-linked video still carries v["web"] for its badge, video-page line and ranking bonus.
        self.web_curated = any((v.get("web") or {}).get("curated") for v in self.videos)
        for v in self.videos:
            sources = list(v.get("comm") if "comm" in v else {"hn": {}})
            if self.web_member(v):
                sources.append(WEB_COMM)
            for c in sources:
                info = meta.get(c, {"label": "Hacker News" if c == "hn" else WEB_LABEL if c == WEB_COMM else c,
                                    "short": "HN" if c == "hn" else WEB_SHORT if c == WEB_COMM else c})
                d = self.comms.setdefault(c, {"id": c, "slug": c.replace(":", "-"), "label": info["label"],
                                              "short": info.get("short", c), "vids": []})
                d["vids"].append(v["vid"])
        if VIEWERS in self.comms:  # vouched submissions: the build labels unknown communities by their id
            self.comms[VIEWERS].update(label=VIEWERS_LABEL, short="Viewers")
            for v in self.videos:
                fix_viewers(v)
        self.comm_by_slug = {d["slug"]: d for d in self.comms.values()}
        self.web_only = {v["vid"] for v in self.videos if web_only(v)}
        self._lib, self._tcache = None, {}
        self.channel_ids = {}
        for v in self.videos:
            self.channel_ids.setdefault(v["channel_id"], []).append(v["vid"])
        good = [v for v in self.videos if v["shelf"] != "news" and (self.web_only_rows or v["vid"] not in self.web_only)]
        self.ranked = sorted(good, key=lambda v: -v["score"])
        self.essentials = [v["vid"] for v in self.ranked[:500]]
        self.gems = sorted([v for v in good if v["views"] < 60_000 and v["hn"]["recommenders"] >= 3],
                           key=lambda v: -v["score"])
        self.recent = sorted([v for v in good if (v["hn"].get("last_year") or 0) >= 2025 and v["hn"]["recommenders"] >= 2],
                             key=lambda v: -v["score"])
        self.longs = sorted([v for v in good if v["dur"] >= 3600], key=lambda v: -v["score"])
        self.fresh = sorted([v for v in good if (v["hn"].get("last_year") or 0) >= 2025 and v["published"] >= "2023-01-01"],
                            key=lambda v: (v["published"], v["score"]), reverse=True)
        self.area_of_cat = {c["id"]: a["id"] for a in self.areas for c in a["cats"]}
        self.area_by_id = {a["id"]: a for a in self.areas}
        self._build_starter()
        self._build_web_view()
        self._model = None
        self._lock = threading.Lock()

    # ---------- embeddings ----------
    # ---------------- the first screen ----------------
    STARTER_KINDS = {"talk", "lecture", "explainer", "documentary", "tutorial"}
    STARTER_NO_WEB = {"arts-entertainment", "games"}  # the open web's picks there are celebrity and promo clips
    # launches and announcements collect links because they were news, not because they last
    STARTER_NEWSY = re.compile(r"\b(keynote|unveil\w*|introducing|announc\w*|launch\w*|event|reveal\w*|walkthrough|presentation|live demo|"
                               r"official|trailer|wwdc|i/o|re:invent|ces \d{4})\b", re.I)

    def _build_starter(self, per_area=3):
        """For each area, the strongest few evergreen videos a community proved and the open web proved.
        A first-time visitor sees one of each, across different fields, before being asked for anything.
        Without YouTube's view counts and dates (the public data release) those two checks are skipped."""
        self.starter_pool = {"comm": [], "web": []}
        comm_by_area, web_by_area = {}, {}
        year_ago = (datetime.date.today() - datetime.timedelta(days=365)).isoformat()  # an open-web pick must have lasted a year
        for v in sorted(self.videos, key=lambda x: -x["score"]):
            if v["shelf"] != "evergreen" or 0 < v["dur"] < 300:
                continue
            w = v.get("web") or {}
            if self.STARTER_NEWSY.search(v["title"]):
                continue
            if v.get("comm") and (v["views"] >= 100_000 or not v["views"]) and max(st.get("recommenders", 0) for st in v["comm"].values()) >= 8:
                for aid in dict.fromkeys(self.area_of_cat.get(c) for c in v["cats"]):
                    if aid and len(comm_by_area.setdefault(aid, [])) < per_area:
                        comm_by_area[aid].append(v)
            if (w.get("curated") and w.get("area") in self.area_by_id and w.get("area") not in self.STARTER_NO_WEB
                    and w.get("kind") in self.STARTER_KINDS and (v["views"] >= 200_000 or not v["views"])
                    and v["published"][:10] <= year_ago):
                web_by_area.setdefault(w["area"], []).append(v)
        for aid, vs in web_by_area.items():
            vs.sort(key=lambda v: (-v["web"].get("reputable", 0), -v["web"]["domains"]))
            del vs[per_area:]
        order = [a["id"] for a in self.areas] + [aid for aid in web_by_area if aid not in self.area_by_id]
        self.starter_pool = {"comm": [(aid, comm_by_area[aid]) for aid in order if comm_by_area.get(aid)],
                             "web": [(aid, web_by_area[aid]) for aid in order if web_by_area.get(aid)]}

    def starter(self, n=8, seed="", skip=()):
        """Alternate a community pick and an open-web pick, each from a field not shown yet; the seed rotates
        which of an area's top few appears, so a returning visitor sees a different first screen each day."""
        skip, out, used = set(skip), [], set()
        def pick(src, allow_used):
            for aid, vs in self.starter_pool[src]:
                if aid in used and not allow_used:
                    continue
                cands = [v for v in vs if v["vid"] not in skip]
                if cands:
                    v = cands[zlib.crc32((seed + aid + src).encode()) % len(cands)]
                    used.add(aid); skip.add(v["vid"])
                    return v
            return None
        for i in range(4 * n):
            if len(out) >= n:
                break
            src = ("comm", "web")[i % 2]
            v = pick(src, False) or pick(("web", "comm")[i % 2], False) or pick(src, True)
            if v is None:
                break
            out.append(v)
        return out

    def _build_web_view(self):
        """The open-web page's order (reputable sites, then all sites, months, score) and field counts, once per load."""
        members = [self.by_vid[x] for x in (self.comms.get(WEB_COMM) or {}).get("vids", [])]
        self.web_order = sorted(members, key=lambda v: (-v["web"].get("reputable", 0), -v["web"]["domains"], -v["web"]["months"], -v["score"]))
        self.web_by_area = {}
        for v in self.web_order:
            self.web_by_area.setdefault(self.area_of(v), []).append(v)
        self.web_counts = collections.Counter({a: len(vs) for a, vs in self.web_by_area.items() if a})

    def area_of(self, v):
        """The field a video belongs to: the gate's area for open-web videos, else the area of its first topic."""
        a = (v.get("web") or {}).get("area")
        if a in self.area_by_id:
            return a
        return next((self.area_of_cat[c] for c in v.get("cats") or [] if c in self.area_of_cat), None)

    def proof(self, v):
        """One line of who vouched for a video: the community with the most recommenders, then the open web."""
        best = max((v.get("comm") or {}).items(), key=lambda kv: kv[1].get("recommenders", 0), default=None)
        w = v.get("web") or {}
        parts = []
        if best and best[1].get("recommenders", 0) >= 5:
            cid, st = best
            label = (self.comms.get(cid) or {}).get("label") or ("Hacker News" if cid == "hn" else cid)
            since = f" since {st['first_year']}" if st.get("first_year") and st.get("first_year") != st.get("last_year") else ""
            parts.append(f"Recommended by {st['recommenders']} people on {label}{since}")
        if w.get("domains", 0) >= 3:
            doms = sorted(w.get("top_domains") or [], key=lambda d: 0 if re.search(r"\.(edu|gov|ac\.[a-z]{2}|edu\.[a-z]{2}|gov\.[a-z]{2})$", d) else 1)[:2]
            if parts:
                parts.append(f"linked by {w['domains']} websites")
            else:
                parts.append(f"Linked by {w['domains']} websites" + (f", including {' and '.join(doms)}" if doms else ""))
        return ", ".join(parts)

    def model(self):
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding
                self._model = TextEmbedding("BAAI/bge-small-en-v1.5",
                                            cache_dir=os.environ.get("FASTEMBED_CACHE_PATH"))
            return self._model

    def embed(self, texts):
        vecs = np.array(list(self.model().embed(texts)), dtype=np.float32)
        vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
        return vecs

    # ---------- feeds ----------
    def daily_mix(self, pool, n, salt=""):
        seed = datetime.date.today().isoformat() + salt
        rng = random.Random(seed)
        pool = list(pool)
        weights = [max(v["score"], 1.0) for v in pool]
        picked, seen = [], set()
        while len(picked) < min(n, len(pool)):
            v = rng.choices(pool, weights=weights, k=1)[0]
            if v["vid"] not in seen:
                seen.add(v["vid"])
                picked.append(v)
        return picked

    def get(self, vid):
        return self.by_vid.get(vid) or ADDED.get(vid)

    def similar(self, vid, n=12):
        v = self.by_vid[vid]
        sims = self.emb @ self.emb[v["i"]]
        out = []
        for i in np.argsort(-sims)[1:n + 1]:
            out.append(self.videos[int(i)])
        return out

    def related_cats(self, cid, n=8):
        """Topics that share the most videos with this one, then siblings from the same area."""
        c = self.cats.get(cid)
        if not c:
            return []
        shared = sorted(((self._cat_overlap.get((cid, o), 0), o) for o in self.cats if o != cid and self.cats[o]["vids"]),
                        reverse=True)
        out = [o for k, o in shared if k > 0][:n]
        for o in self.cats:
            if len(out) >= n:
                break
            if o != cid and o not in out and self.cats[o]["area_id"] == c["area_id"] and self.cats[o]["vids"]:
                out.append(o)
        return out[:n]

    def co_recommended(self, vid, n=8):
        return [self.by_vid[x] for x in self.related.get(vid, [])[:n] if x in self.by_vid]

    def search(self, q, n=48):
        q = q.strip()
        if not q:
            return []
        ql = q.lower()
        scores = np.zeros(len(self.videos), dtype=np.float32)
        try:
            qv = self.embed([q])[0]
            scores += self.emb @ qv
        except Exception:
            pass
        for v in self.videos:
            hay = (v["title"] + " " + v["channel"]).lower()
            if ql in hay:
                scores[v["i"]] += 0.35
            elif all(t in hay for t in ql.split()):
                scores[v["i"]] += 0.2
        scores += np.array([min(v["score"], 30) for v in self.videos], dtype=np.float32) * 0.004
        return [self.videos[int(i)] for i in np.argsort(-scores)[:n]]

    def personal(self, vec, exclude=(), boost_channels=(), boost_vids=(), n=48):
        if vec is None:
            return []
        v = np.asarray(vec, dtype=np.float32)
        v /= np.linalg.norm(v) + 1e-9
        scores = self.emb @ v
        scores += np.array([min(x["score"], 30) for x in self.videos], dtype=np.float32) * 0.006
        for ch in boost_channels:
            for vid in self.channel_ids.get(ch, []):
                scores[self.by_vid[vid]["i"]] += 0.08
        for vid in boost_vids:
            if vid in self.by_vid:
                scores[self.by_vid[vid]["i"]] += 0.05
        ex = set(exclude)
        out = []
        for i in np.argsort(-scores):
            x = self.videos[int(i)]
            if x["vid"] in ex or x["shelf"] == "news":
                continue
            out.append(x)
            if len(out) >= n:
                break
        return out

    def embed_cached(self, texts):
        """Embeddings of short texts such as interest names, remembered: the same phrases come up again and again."""
        need = [t for t in dict.fromkeys(texts) if t not in self._tcache]
        if need:
            if len(self._tcache) > 5000:
                self._tcache.clear()
            self._tcache.update(zip(need, self.embed(need)))
        return [self._tcache[t] for t in texts]

    def _lib_terms(self):
        """What the librarian adds to relevance for each video, computed once per catalogue: how often its
        communities recommend it, and whether a community recommended it at all (a link count alone is weaker
        evidence). Together they span well under the gap between a strong and a middling match, so they
        break ties and never decide."""
        if self._lib is None:
            score = np.array([min(v["score"], 30) for v in self.videos], dtype=np.float32)
            webo = np.array([v["vid"] in self.web_only for v in self.videos])
            rep = np.array([(v.get("web") or {}).get("reputable", 0) >= 2 for v in self.videos])
            prior = 0.6 * np.log1p(score) / np.log1p(30) + np.where(webo, np.where(rep, 0.1, 0.0), 0.5)
            self._lib = {"prior": prior.astype(np.float32),
                         "dur": np.array([v["dur"] or 0 for v in self.videos], dtype=np.float32)}
        return self._lib

    def librarian(self, vec, anchors=(), exclude=(), followed=(), boost_vids=(), fmt="", n=60):
        """A personal shortlist and the reason each pick is on it: (videos, {vid: reason}).

        `vec` is the person's overall taste. `anchors` are their separate interests, as
        (kind, reason, vector, bias, vid) tuples: kind "topic" is something they told their AI about,
        "item" a video they liked, watched or saved (vid is that video), "source" a whole connected source.
        A video's relevance is its best match to any one interest, blended with the overall taste. Each match
        is measured against how close that interest is to the catalogue as a whole, so a narrow interest isn't
        drowned by a broad one and a mix of interests doesn't blur into the site's general favourites.
        Recommendation counts only break ties (_lib_terms). Channels the person follows (`followed`) take the
        LIB_FOLLOWED_AT slots of the first screen, and the rest of it goes to creators they may not know yet.
        On the first screen no channel appears twice and no interest takes more than its share. `fmt` is the
        length someone said they prefer ("short" or "long"); it doesn't apply to channels they follow, which
        they already watch at that channel's length. The video an "item" reason points at is never picked."""
        if vec is None:
            return [], {}
        terms = self._lib_terms()
        A = np.stack([np.asarray(x, dtype=np.float32) for x in [vec] + [a[2] for a in anchors]])
        A /= np.linalg.norm(A, axis=1, keepdims=True) + 1e-9
        S = self.emb @ A.T
        Z = (S - S.mean(axis=0)) / (S.std(axis=0) + 1e-6)
        if anchors:
            ZI = Z[:, 1:].copy()
            items = [k for k, a in enumerate(anchors) if a[0] == "item"]
            if items:  # near-copies of a video someone liked (a sequel, a re-upload) count as a strong match, no more
                ZI[:, items] = np.minimum(ZI[:, items], LIB_ITEM_MAX)
            ZI += np.array([a[3] for a in anchors], dtype=np.float32)
            win = ZI.argmax(axis=1)
            rel = 0.7 * ZI[np.arange(len(ZI)), win] + 0.3 * Z[:, 0]
        else:
            win, rel = None, Z[:, 0]
        dur = terms["dur"]
        fit = -np.where((dur > 0) & (dur < LIB_SHORT_CLIP), 0.4, 0.0)   # unknown length: no penalty
        if fmt == "short":
            fit -= np.where(dur > 2400, 0.5, np.where(dur > 1200, 0.2, 0.0))
        elif fmt == "long":
            fit += np.where(dur >= 1500, 0.2, 0.0)
        isf = np.zeros(len(self.videos), dtype=bool)
        fol = set(followed)
        for ch in fol:
            for vid in self.channel_ids.get(ch, []):
                isf[self.by_vid[vid]["i"]] = True
        final = rel + terms["prior"] + np.where(isf, 0.0, fit)
        for vid in boost_vids:
            x = self.by_vid.get(vid)
            if x:
                final[x["i"]] += 0.3
        ex = set(exclude) | {a[4] for a in anchors if a[0] == "item"}
        vids = self.videos

        def ok(i):
            return vids[i]["vid"] not in ex and vids[i]["shelf"] != "news"

        def key(i):
            return "f" if isf[i] else (int(win[i]) if win is not None else -1)

        def cap(k):
            return 1 if (k not in ("f", -1) and anchors[k][0] == "item") else LIB_PER_INTEREST

        out, used, chans, per = [], set(), collections.Counter(), collections.Counter()

        def take(i):
            out.append(vids[i])
            used.add(vids[i]["vid"])
            chans[vids[i]["channel"]] += 1
            per[key(i)] += 1

        def fresh(i):
            return ok(i) and vids[i]["vid"] not in used and not chans[vids[i]["channel"]]

        order = [int(i) for i in np.argsort(-final)[:4000]]
        fidx = np.flatnonzero(isf)
        familiar = iter(int(i) for i in fidx[np.argsort(-final[fidx])])
        for slot in range(min(LIB_FIRST, n)):
            if slot in LIB_FOLLOWED_AT:
                i = next((i for i in familiar if fresh(i)), None)
                if i is not None:
                    take(i)
                    continue
            i = next((i for i in order if not isf[i] and fresh(i) and per[key(i)] < cap(key(i))), None)
            if i is None:  # few interests: let one take more of the screen, but still one pick per liked video
                i = next((i for i in order if not isf[i] and fresh(i) and not (cap(key(i)) == 1 and per[key(i)])), None)
            if i is None:  # and when nothing else is left, fill the screen anyway
                i = next((i for i in order if not isf[i] and fresh(i)), None)
            if i is not None:
                take(i)
        for i in order:
            if len(out) >= n:
                break
            if ok(i) and vids[i]["vid"] not in used and chans[vids[i]["channel"]] < 2:
                take(i)
        reasons = {}
        for x in out:
            i = x["i"]
            reasons[x["vid"]] = (f"From {x['channel']}, which you follow" if isf[i]
                                 else anchors[int(win[i])][1] if win is not None else "")
        return out, reasons

    def user_vector(self, catalog_vids=(), texts=()):
        parts = []
        for vid in catalog_vids:
            if vid in self.by_vid:
                parts.append(self.emb[self.by_vid[vid]["i"]] * 2.0)
        if texts:
            parts.extend(self.embed(list(texts)[:250]))
        if not parts:
            return None
        m = np.mean(np.stack(parts), axis=0)
        m /= np.linalg.norm(m) + 1e-9
        return [round(float(x), 5) for x in m]

    # ---------- profile building ----------
    def author_recs(self, handle):
        """Catalogue videos this HN user recommended, from the open dataset."""
        return list(self.authors.get((handle or "").lower(), []))

    def co_recommended(self, vids, n=60):
        out = []
        for vid in vids:
            out += self.related.get(vid, [])
        return [v for v, _ in collections.Counter(out).most_common(n)]

    def text_vector(self, texts, limit=300):
        texts = [t.strip()[:400] for t in texts if t and t.strip()][:limit]
        if not texts:
            return None
        m = np.mean(self.embed(texts), axis=0)
        m /= np.linalg.norm(m) + 1e-9
        return [round(float(x), 5) for x in m]

    def vids_vector(self, vids):
        rows = [self.emb[self.by_vid[v]["i"]] for v in vids if v in self.by_vid]
        if not rows:
            return None
        m = np.mean(np.stack(rows), axis=0)
        m /= np.linalg.norm(m) + 1e-9
        return [round(float(x), 5) for x in m]

    def combine(self, parts):
        """Weighted mean of unit vectors: parts = [(weight, vec), ...]."""
        parts = [(w, np.asarray(v, dtype=np.float32)) for w, v in parts if v is not None and w > 0]
        if not parts:
            return None
        m = sum(w * v for w, v in parts) / sum(w for w, _ in parts)
        m /= np.linalg.norm(m) + 1e-9
        return [round(float(x), 5) for x in m]

    def interests(self, vec, n=6):
        """Human-readable interest labels: the topics of the closest catalogue videos."""
        if vec is None:
            return []
        v = np.asarray(vec, dtype=np.float32)
        sims = self.emb @ v
        counts = collections.Counter()
        for rank, i in enumerate(np.argsort(-sims)[:120]):
            vid = self.videos[int(i)]
            w = float(sims[i]) * (1.0 if rank < 40 else 0.6)
            for k, cid in enumerate(vid["cats"][:2]):
                if cid in self.cats:
                    counts[self.cats[cid]["name"]] += w * (1.0 if k == 0 else 0.5)
        return [name for name, _ in counts.most_common(n)]

    # ---------- screenshot title matching ----------
    def match_titles(self, items):
        """Map titles read from a screenshot to catalogue videos: exact, truncated-prefix, then embedding."""
        matched, unmatched, pending, seen = [], [], [], set()
        for it in items:
            t = re.sub(r"[.\u2026\s]+$", "", (it.get("title") or "").strip())
            if len(t) < 4:
                continue
            k = norm_key(t)
            vid = self.title_index.get(k)
            if not vid and len(k) >= 18:
                cands = [v for kk, v in self.title_keys if kk.startswith(k)]
                vid = cands[0] if len(cands) == 1 else None
            if vid:
                if vid not in seen:
                    seen.add(vid)
                    matched.append(vid)
            else:
                pending.append(dict(it, title=t))
        if pending:
            q = self.embed([f"{p['title']} {p.get('channel') or ''}".strip() for p in pending])
            sims = q @ self.title_emb.T
            for p, row in zip(pending, sims):
                j = int(np.argmax(row))
                v = self.videos[j]
                if row[j] >= 0.86 and jaccard(norm_key(p["title"]).split(), norm_key(v["title"]).split()) >= 0.5:
                    if v["vid"] not in seen:
                        seen.add(v["vid"])
                        matched.append(v["vid"])
                else:
                    unmatched.append(p)
        return matched, unmatched

    # ---------- filtered feeds for the home page ----------
    WEB_MIN_SECONDS = 180  # the open-web page promises talks, lectures and explainers over three minutes

    def web_member(self, v):
        """True when the video belongs to "The open web" community: curated when the pack marks a curated pool,
        and at least three minutes long (the gate does not apply its length rule reliably). Data without YouTube's
        durations (the public release) can't apply that rule, so an unknown length passes."""
        web = v.get("web")
        return bool(web) and (web.get("curated", False) or not self.web_curated) and (not v.get("dur") or v["dur"] >= self.WEB_MIN_SECONDS)

    def sources_of(self, v):
        """Community ids a video carries, with "web" for the open-web source."""
        out = set(v.get("comm") if "comm" in v else {"hn": 1})
        if self.web_member(v):
            out.add(WEB_COMM)
        return out

    def in_scope(self, areas=(), communities=()):
        areas, communities = set(areas), set(communities)
        with_web_only = self.web_only_rows or WEB_COMM in communities
        if not areas and not communities:
            return [v for v in self.videos if v["shelf"] != "news" and (with_web_only or v["vid"] not in self.web_only)]
        out = []
        for v in self.videos:
            if v["shelf"] == "news":
                continue
            if not with_web_only and v["vid"] in self.web_only:
                continue
            if communities and not (self.sources_of(v) & communities):
                continue
            if areas and not any(self.area_of_cat.get(c) in areas for c in v["cats"]):
                continue
            out.append(v)
        return out

    def feed_rows(self, areas=(), communities=(), seed=""):
        pool = self.in_scope(areas, communities)
        by_score = sorted(pool, key=lambda v: -v["score"])
        fresh = sorted([v for v in pool if (v["hn"].get("last_year") or 0) >= 2025 and v["published"] >= "2023-01-01"],
                       key=lambda v: (v["published"], v["score"]), reverse=True)
        gems = [v for v in by_score if v["views"] < 60_000 and v["recs"] >= 3]
        rows = []
        if len(fresh) >= 4:
            rows.append(("Fresh picks", "Recommended in the last year, newest first", fresh[:8]))
        rows.append(("Most recommended", "Ranked by how many separate months people recommended them", self.daily_mix(by_score[:200], 12, seed + "top")))
        if len(gems) >= 4:
            rows.append(("Hidden gems", "Loved by the community, under 60,000 views", self.daily_mix(gems[:150], 8, seed + "gem")))
        return rows, len(pool)
