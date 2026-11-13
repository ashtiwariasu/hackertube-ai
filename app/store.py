"""User storage: Firestore in production, in-memory locally."""
import datetime, os

COLL = "ht_users"


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class MemoryStore:
    def __init__(self):
        self.users = {}

    def get(self, uid):
        return self.users.get(uid)

    def upsert(self, uid, data):
        self.users.setdefault(uid, {"created": now()}).update(data)
        return self.users[uid]

    def replace(self, uid, doc):
        self.users[uid] = dict(doc)
        return self.users[uid]

    def find(self, path, value):
        """[(uid, doc)] whose field at a dotted path equals value."""
        out = []
        for uid, doc in self.users.items():
            cur = doc
            for k in path.split("."):
                cur = cur.get(k) if isinstance(cur, dict) else None
            if cur == value:
                out.append((uid, dict(doc)))
        return out


class FirestoreStore:
    def __init__(self):
        from google.cloud import firestore
        self.db = firestore.Client(project=os.environ.get("GCP_PROJECT") or None)

    def get(self, uid):
        doc = self.db.collection(COLL).document(uid).get()
        return doc.to_dict() if doc.exists else None

    def upsert(self, uid, data):
        ref = self.db.collection(COLL).document(uid)
        snap = ref.get()
        if not snap.exists:
            data = {"created": now(), **data}
        ref.set(data, merge=True)
        return ref.get().to_dict()

    def replace(self, uid, doc):
        """Overwrite the whole document (merge can't remove map keys)."""
        self.db.collection(COLL).document(uid).set(dict(doc))
        return doc

    def find(self, path, value):
        from google.cloud.firestore_v1.base_query import FieldFilter
        return [(s.id, s.to_dict()) for s in self.db.collection(COLL).where(filter=FieldFilter(path, "==", value)).stream()]


def make_store():
    return FirestoreStore() if os.environ.get("STORE", "memory") == "firestore" else MemoryStore()
