"""Điểm vào chung:  python -m src.pipeline.run [crawl|clean|extract|images|merge|all] [--source NAME] [--force]"""
from __future__ import annotations

import argparse
import json
import logging

from .clean import clean_source
from .config import CHUNKS_DIR, EMBEDDINGS_DIR, get_sources, load_config
from .crawl import crawl_source
from .extract import run_extract
from .images import run_images
from .merge import run_merge
from .tag import run_tag
from .chunk import run_chunk
from .utils import read_jsonl
from .vector_search import DEFAULT_MODEL, load_encoder
from .vector_search import run_embed, run_evaluate, search


def main() -> None:
    ap = argparse.ArgumentParser(description="Pipeline crawl -> extract -> merge -> tag -> chunk -> embeddings")
    ap.add_argument(
        "stage",
        choices=["crawl", "clean", "extract", "images", "merge", "tag", "chunk", "embed", "search",
                 "evaluate-search", "all"],
        nargs="?",
        default="all",
    )
    ap.add_argument("--source", help="chỉ chạy 1 nguồn (tên trong sources.yaml)")
    ap.add_argument("--force", action="store_true", help="crawl/tải ảnh lại kể cả mục đã có")
    ap.add_argument("--query", help="câu truy vấn (dùng với stage search)")
    ap.add_argument("--top-k", type=int, default=5, help="số kết quả tìm kiếm / ngưỡng đánh giá")
    ap.add_argument("--model", default=DEFAULT_MODEL, help="tên model Sentence Transformers")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    cfg = load_config()
    sources = get_sources(cfg, args.source)

    if args.stage in ("crawl", "all"):
        for s in sources:
            print(f"[crawl:{s['name']}]", json.dumps(crawl_source(s, cfg["defaults"], args.force)))
    if args.stage in ("clean", "all"):
        for s in sources:
            print(f"[clean:{s['name']}]", json.dumps(clean_source(s)))
    if args.stage in ("extract", "all"):
        print("[extract]", json.dumps(run_extract(sources), ensure_ascii=False, indent=2))
    if args.stage in ("images", "all"):
        print("[images]", json.dumps(run_images(sources, cfg["defaults"], args.force), ensure_ascii=False, indent=2))
    if args.stage in ("merge", "all"):
        order = [s["name"] for s in cfg["sources"]]  # thứ tự trong sources.yaml = thứ tự ưu tiên khi gộp
        print("[merge]", json.dumps(run_merge(order), ensure_ascii=False, indent=2))
    if args.stage in ("tag", "all"):
        print("[tag]", json.dumps(run_tag(), ensure_ascii=False, indent=2))
    if args.stage in ("chunk", "all"):
        print("[chunk]", json.dumps(run_chunk(), ensure_ascii=False, indent=2))
    if args.stage in ("embed", "all"):
        print("[embed]", json.dumps(run_embed(args.model), ensure_ascii=False, indent=2))
    if args.stage == "search":
        if not args.query:
            ap.error("stage search requires --query")
        chunks = read_jsonl(CHUNKS_DIR / "chunks.jsonl")
        embeddings = read_jsonl(EMBEDDINGS_DIR / "venue_embeddings.jsonl")
        encoder = load_encoder(args.model)
        results = search(args.query, chunks, embeddings, encoder, args.top_k)
        print(json.dumps([
            {"venue_key": row["chunk"]["venue_key"], "name": row["chunk"]["metadata"]["canonical_name"],
             "score": round(row["score"], 4), "metadata": row["chunk"]["metadata"]}
            for row in results
        ], ensure_ascii=False, indent=2))
    if args.stage == "evaluate-search":
        print(json.dumps(run_evaluate(args.model, args.top_k), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
