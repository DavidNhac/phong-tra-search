"""Điểm vào chung:  python -m src.pipeline.run [crawl|clean|extract|images|merge|all] [--source NAME] [--force]"""
from __future__ import annotations

import argparse
import json
import logging

from .clean import clean_source
from .config import get_sources, load_config
from .crawl import crawl_source
from .extract import run_extract
from .images import run_images
from .merge import run_merge


def main() -> None:
    ap = argparse.ArgumentParser(description="Pipeline bước 1-2: crawl -> clean -> extract -> images -> merge")
    ap.add_argument("stage", choices=["crawl", "clean", "extract", "images", "merge", "all"], nargs="?", default="all")
    ap.add_argument("--source", help="chỉ chạy 1 nguồn (tên trong sources.yaml)")
    ap.add_argument("--force", action="store_true", help="crawl/tải ảnh lại kể cả mục đã có")
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


if __name__ == "__main__":
    main()
