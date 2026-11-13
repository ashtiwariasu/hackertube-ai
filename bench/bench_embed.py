"""Retrieval benchmark: comment -> the video it was posted about.
Card-only index (one vector per video) and multi-vector index (card + other comments, leave-one-out)."""
import json, sys, time
import numpy as np, torch
from sentence_transformers import SentenceTransformer

D = json.load(open("data.json"))
docs, mentions, qids = D["docs"], D["mentions"], set(D["queries"])
vids = [d["vid"] for d in docs]
vid_row = {v: i for i, v in enumerate(vids)}
m_vid = np.array([vid_row[m["vid"]] for m in mentions])
q_idx = [i for i, m in enumerate(mentions) if m["id"] in qids]
MODELS = {
    "bge-small-en-v1.5": dict(name="BAAI/bge-small-en-v1.5", q="", d=""),
    "bge-base-en-v1.5": dict(name="BAAI/bge-base-en-v1.5", q="Represent this sentence for searching relevant passages: ", d=""),
    "bge-large-en-v1.5": dict(name="BAAI/bge-large-en-v1.5", q="Represent this sentence for searching relevant passages: ", d=""),
    "e5-large-v2": dict(name="intfloat/e5-large-v2", q="query: ", d="passage: "),
    "nomic-embed-text-v1.5": dict(name="nomic-ai/nomic-embed-text-v1.5", q="search_query: ", d="search_document: ", trc=True),
    "qwen3-embedding-0.6b": dict(name="Qwen/Qwen3-Embedding-0.6B", q="Instruct: Given a Hacker News comment, retrieve the video it is about\nQuery: ", d=""),
    "embeddinggemma-300m": dict(name="google/embeddinggemma-300m", alt="unsloth/embeddinggemma-300m",
                                q="task: search result | query: ", d="title: none | text: ", dtype=torch.bfloat16),
}
only = sys.argv[1:] or list(MODELS)
results = json.load(open("results.json")) if __import__("os").path.exists("results.json") else {}

def metrics(ranked_rows, truth):
    r1 = r5 = r10 = mrr = 0.0
    for row, t in zip(ranked_rows, truth):
        hit = np.where(row[:10] == t)[0]
        if len(hit):
            k = hit[0]
            mrr += 1 / (k + 1); r10 += 1; r5 += k < 5; r1 += k == 0
    n = len(truth)
    return dict(r1=r1 / n, r5=r5 / n, r10=r10 / n, mrr10=mrr / n)

for key in only:
    cfg = MODELS[key]
    t0 = time.time()
    try:
        try:
            model = SentenceTransformer(cfg["name"], device="cuda", trust_remote_code=cfg.get("trc", False),
                                        model_kwargs={"torch_dtype": cfg.get("dtype", torch.float16)})
        except Exception as e:
            if not cfg.get("alt"):
                raise
            print(key, "primary repo failed (likely gated), trying mirror:", str(e)[:80], flush=True)
            model = SentenceTransformer(cfg["alt"], device="cuda", trust_remote_code=cfg.get("trc", False),
                                        model_kwargs={"torch_dtype": cfg.get("dtype", torch.float16)})
        model.max_seq_length = min(model.max_seq_length or 512, 1024)
        de = model.encode([cfg["d"] + d["text"] for d in docs], batch_size=128, normalize_embeddings=True, convert_to_numpy=True)
        me = model.encode([cfg["d"] + m["text"] for m in mentions], batch_size=128, normalize_embeddings=True, convert_to_numpy=True)
        qe = model.encode([cfg["q"] + mentions[i]["text"] for i in q_idx], batch_size=128, normalize_embeddings=True, convert_to_numpy=True)
    except Exception as e:
        print(key, "FAILED:", str(e)[:200], flush=True)
        results[key] = {"error": str(e)[:300]}
        json.dump(results, open("results.json", "w"), indent=1)
        continue
    de, me, qe = (torch.tensor(x, device="cuda", dtype=torch.float32) for x in (de, me, qe))
    print(key, "nan embeddings:", int(torch.isnan(de).any(dim=1).sum()), flush=True)
    truth = m_vid[q_idx]
    # card-only
    sims = qe @ de.T
    card = metrics(torch.topk(sims, 10, dim=1).indices.cpu().numpy(), truth)
    # multi-vector: per video max over card + its comments, excluding the query comment itself
    msims = qe @ me.T
    q_pos = torch.tensor(q_idx, device="cuda")
    msims[torch.arange(len(q_idx), device="cuda"), q_pos] = -1.0
    per_vid = torch.full((len(q_idx), len(vids)), -1.0, device="cuda")
    per_vid.scatter_reduce_(1, torch.tensor(m_vid, device="cuda").unsqueeze(0).expand(len(q_idx), -1), msims, reduce="amax")
    multi = metrics(torch.topk(torch.maximum(per_vid, sims), 10, dim=1).indices.cpu().numpy(), truth)
    results[key] = {"card": card, "multi": multi, "dim": int(de.shape[1]), "seconds": round(time.time() - t0, 1)}
    print(f"{key:24s} dim={de.shape[1]:4d} card R@1 {card['r1']:.3f} R@5 {card['r5']:.3f} R@10 {card['r10']:.3f} MRR {card['mrr10']:.3f} | "
          f"multi R@1 {multi['r1']:.3f} R@5 {multi['r5']:.3f} R@10 {multi['r10']:.3f} MRR {multi['mrr10']:.3f} | {results[key]['seconds']}s", flush=True)
    json.dump(results, open("results.json", "w"), indent=1)
    del model, de, me, qe, sims, msims, per_vid
    torch.cuda.empty_cache()
