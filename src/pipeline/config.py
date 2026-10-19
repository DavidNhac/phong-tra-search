"""Đường dẫn và loader cấu hình dùng chung cho cả pipeline."""
from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "sources.yaml"

DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "01_raw_html"        # HTML gốc đúng như server trả về
CLEAN_DIR = DATA_DIR / "02_clean_html"    # HTML đã bỏ nhiễu, chỉ còn vùng nội dung
EXTRACT_DIR = DATA_DIR / "03_extracted"   # JSONL có cấu trúc, đầu vào cho bước chunking
IMAGES_DIR = EXTRACT_DIR / "images"       # ảnh đã tải: images/<source>/<sha1>.jpg + manifest.json
MERGED_DIR = DATA_DIR / "04_merged"       # mỗi quán 1 bản ghi duy nhất sau khi gộp các nguồn
TAGGED_DIR = DATA_DIR / "05_tagged"       # metadata chuẩn hoá cho lọc và chunking
CHUNKS_DIR = DATA_DIR / "04_chunks"       # mỗi venue là một đơn vị ngữ nghĩa ban đầu
EMBEDDINGS_DIR = DATA_DIR / "06_embeddings"
ALIASES_PATH = ROOT / "config" / "venue_aliases.yaml"  # tuỳ chọn: ép tên chuẩn / ép gộp


def load_config(path: Path | None = None) -> dict:
    with open(path or CONFIG_PATH, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg.setdefault("defaults", {})
    cfg.setdefault("sources", [])
    return cfg


def get_sources(cfg: dict, only: str | None = None) -> list[dict]:
    sources = cfg["sources"]
    if only:
        sources = [s for s in sources if s["name"] == only]
        if not sources:
            raise SystemExit(f"Không có source tên '{only}' trong sources.yaml")
    return sources
