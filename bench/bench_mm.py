"""Multimodal benchmark with EmbeddingGemma 2: does adding public thumbnail frames to each video's
document embedding improve comment -> video retrieval? Same 4,000 queries as bench_embed.py."""
import json, os, sys, time
import numpy as np, torch
from sentence_transformers import SentenceTransformer

D = json.load(open("data.json"))
docs, mentions, qids = D["docs"], D["mentions"], set(D["queries"])
vid_row = {d["vid"]: i for i, d in enumerate(docs)}
q = [m for m in mentions if m["id"] in qids]
truth = np.array([vid_row[m["vid"]] for m in q])
FRAMES = ["hq1", "hq2", "hq3", "hqdefault"]
frames = {d["vid"]: [f"thumbs/{d['vid']}_{n}.jpg" for n in FRAMES if os.path.getsize(f"thumbs/{d['vid']}_{n}.jpg") > 1000]
          if all(os.path.exists(f"thumbs/{d['vid']}_{n}.jpg") for n in FRAMES) else
          [f"thumbs/{d['vid']}_{n}.jpg" for n in FRAMES if os.path.exists(f"thumbs/{d['vid']}_{n}.jpg")]
          for d in docs}
print("videos with 4 frames:", sum(len(v) == 4 for v in frames.values()), "with 0:", sum(len(v) == 0 for v in frames.values()), flush=True)

def split_doc(text):
    title, _, rest = text.partition(". ")
    return title, rest

def metrics(ranked, truth):
    r1 = r5 = r10 = mrr = 0.0
    for row, t in zip(ranked, truth):
        hit = np.where(row[:10] == t)[0]
        if len(hit):
            k = hit[0]; mrr += 1 / (k + 1); r10 += 1; r5 += k < 5; r1 += k == 0
    n = len(truth)
    return dict(r1=round(r1 / n, 4), r5=round(r5 / n, 4), r10=round(r10 / n, 4), mrr10=round(mrr / n, 4))

t0 = time.time()
kw = dict(device="cuda", model_kwargs={"torch_dtype": torch.bfloat16})
try:
    model = SentenceTransformer("google/embeddinggemma-2", config_kwargs={"audio_config": None}, **kw)
except Exception as e:
    print("audio-less load failed, loading full model:", str(e)[:120], flush=True)
    model = SentenceTransformer("google/embeddinggemma-2", **kw)
print("loaded in", round(time.time() - t0, 1), "s; prompts:", list(model.prompts)[:8], flush=True)

qe = model.encode([m["text"] for m in q], prompt_name="SearchQuery", batch_size=64, normalize_embeddings=True, convert_to_tensor=True).float()

def encode_docs(items, bs):
    try:
        return model.encode(items, batch_size=bs, normalize_embeddings=True, convert_to_tensor=True).float()
    except Exception as e:
        print("batch encode failed, falling back to one-by-one:", str(e)[:160], flush=True)
        out = [model.encode(it, normalize_embeddings=True, convert_to_tensor=True).float() for it in items]
        return torch.stack(out)

variants = {}
variants["text only"] = [f"title: {split_doc(d['text'])[0]} | text: {split_doc(d['text'])[1]}" for d in docs]
variants["text + 4 frames"] = [{"text": f"title: {split_doc(d['text'])[0]} | text: {' '.join('<|image|>' for _ in frames[d['vid']])} {split_doc(d['text'])[1]}",
                                "image": frames[d["vid"]]} if frames[d["vid"]] else variants["text only"][i] for i, d in enumerate(docs)]
variants["title + 4 frames"] = [{"text": f"title: {split_doc(d['text'])[0]} | text: {' '.join('<|image|>' for _ in frames[d['vid']])}",
                                 "image": frames[d["vid"]]} if frames[d["vid"]] else f"title: {split_doc(d['text'])[0]} | text: none" for d in docs]
variants["title only"] = [f"title: {split_doc(d['text'])[0]} | text: none" for d in docs]
variants["frames only"] = [{"text": " ".join("<|image|>" for _ in frames[d["vid"]]), "image": frames[d["vid"]]} if frames[d["vid"]] else "title: none | text: none" for d in docs]

only = sys.argv[1:] or list(variants)
results = {}
for name in only:
    t1 = time.time()
    de = encode_docs(variants[name], bs=16 if "frame" in name else 64)
    ranked = torch.topk(qe @ de.T, 10, dim=1).indices.cpu().numpy()
    m = metrics(ranked, truth)
    m["seconds"] = round(time.time() - t1, 1)
    results[name] = m
    print(f"{name:18s} R@1 {m['r1']:.3f} R@5 {m['r5']:.3f} R@10 {m['r10']:.3f} MRR {m['mrr10']:.3f} | {m['seconds']}s", flush=True)
    json.dump(results, open(os.environ.get("OUT", "results_mm.json"), "w"), indent=1)
print("ALL_DONE", flush=True)
