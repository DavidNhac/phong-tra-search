"""Local multilingual E5 embeddings and cosine-similarity retrieval."""
from __future__ import annotations

import json

import yaml

from .config import CHUNKS_DIR, EMBEDDINGS_DIR, ROOT
from .utils import read_jsonl, write_jsonl

DEFAULT_MODEL = "intfloat/multilingual-e5-small"
EVALUATION_PATH = ROOT / "config" / "search_eval.yaml"
EMBEDDINGS_PATH = EMBEDDINGS_DIR / "venue_embeddings.jsonl"


def load_encoder(model_name: str = DEFAULT_MODEL):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name, device="cpu")


def _encode(encoder, texts: list[str], prefix: str):
    return encoder.encode(
        [prefix + text for text in texts],
        batch_size=16,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )


def build_embeddings(chunks: list[dict], encoder, model_name: str) -> list[dict]:
    vectors = _encode(encoder, [chunk["text"] for chunk in chunks], "passage: ")
    return [
        {"chunk_id": chunk["chunk_id"], "model": model_name, "vector": vector.tolist()}
        for chunk, vector in zip(chunks, vectors, strict=True)
    ]


def cosine_ranking(query_vector, chunks: list[dict], embeddings: list[dict], top_k: int = 5) -> list[dict]:
    vectors = {row["chunk_id"]: row["vector"] for row in embeddings}
    scored = []
    for chunk in chunks:
        vector = vectors.get(chunk["chunk_id"])
        if vector is None:
            continue
        score = sum(float(q) * float(v) for q, v in zip(query_vector, vector, strict=True))
        scored.append({"chunk": chunk, "score": score})
    return sorted(scored, key=lambda row: row["score"], reverse=True)[:top_k]


def search(query: str, chunks: list[dict], embeddings: list[dict], encoder, top_k: int = 5) -> list[dict]:
    if not query.strip():
        raise ValueError("Search query must not be empty.")
    query_vector = _encode(encoder, [query.strip()], "query: ")[0]
    return cosine_ranking(query_vector, chunks, embeddings, top_k)


def run_embed(model_name: str = DEFAULT_MODEL) -> dict:
    chunks = read_jsonl(CHUNKS_DIR / "chunks.jsonl")
    if not chunks:
        raise SystemExit("Chưa có data/04_chunks/chunks.jsonl - hãy chạy chunk trước.")
    encoder = load_encoder(model_name)
    embeddings = build_embeddings(chunks, encoder, model_name)
    EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)
    write_jsonl(EMBEDDINGS_PATH, embeddings)
    report = {"chunks_embedded": len(embeddings), "model": model_name, "device": "cpu"}
    (EMBEDDINGS_DIR / "embedding_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report


def evaluate_queries(
    cases: list[dict],
    chunks: list[dict],
    embeddings: list[dict],
    encoder,
    top_k: int = 5,
) -> dict:
    results = []
    for case in cases:
        ranked = search(case["query"], chunks, embeddings, encoder, top_k)
        expected = set(case["relevant_venue_keys"])
        rank = next(
            (index for index, row in enumerate(ranked, start=1) if row["chunk"]["venue_key"] in expected),
            None,
        )
        results.append(
            {
                "query": case["query"],
                "relevant_venue_keys": sorted(expected),
                "top_venue_keys": [row["chunk"]["venue_key"] for row in ranked],
                "hit_at_k": rank is not None,
                "reciprocal_rank": 1 / rank if rank else 0.0,
            }
        )
    total = len(results)
    return {
        "queries": total,
        f"recall_at_{top_k}": sum(result["hit_at_k"] for result in results) / total if total else 0.0,
        "mean_reciprocal_rank": sum(result["reciprocal_rank"] for result in results) / total if total else 0.0,
        "results": results,
    }


def run_evaluate(model_name: str = DEFAULT_MODEL, top_k: int = 5) -> dict:
    chunks = read_jsonl(CHUNKS_DIR / "chunks.jsonl")
    embeddings = read_jsonl(EMBEDDINGS_PATH)
    if not chunks or not embeddings:
        raise SystemExit("Chưa có chunks/embeddings - hãy chạy chunk rồi embed trước.")
    cases = yaml.safe_load(EVALUATION_PATH.read_text(encoding="utf-8"))["queries"]
    encoder = load_encoder(model_name)
    report = evaluate_queries(cases, chunks, embeddings, encoder, top_k)
    report["model"] = model_name
    EMBEDDINGS_DIR.mkdir(parents=True, exist_ok=True)
    (EMBEDDINGS_DIR / "search_eval_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return report
