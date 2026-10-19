"""Create venue-level semantic units from tagged venue records."""
from __future__ import annotations

import json
import re

from .config import CHUNKS_DIR, TAGGED_DIR
from .utils import normalize_text, read_jsonl, write_jsonl

FIELD_LINE_RE = re.compile(
    r"(?i)^\s*(?:địa chỉ|address|giờ mở cửa|giờ hoạt động|thời gian mở cửa|mở cửa|"
    r"giá(?: đồ uống)?(?: từ)?|mức giá|giá vé)\s*:"
)


def _venue_text(venue: dict) -> str:
    parts = [venue["canonical_name"]]
    aliases = [alias for alias in venue.get("aliases", []) if alias != venue["canonical_name"]]
    if aliases:
        parts.append("Tên gọi khác: " + "; ".join(aliases))
    taglines = venue.get("taglines", [])
    if taglines:
        parts.extend(taglines)

    seen = set()
    for record in venue.get("source_records", []):
        description = record.get("description") or ""
        for paragraph in description.splitlines():
            paragraph = normalize_text(paragraph)
            if not paragraph or FIELD_LINE_RE.match(paragraph):
                continue
            key = paragraph.casefold()
            if key not in seen:
                seen.add(key)
                parts.append(paragraph)
    return "\n".join(part for part in parts if part)


def build_chunks(venues: list[dict]) -> list[dict]:
    chunks = []
    for venue in venues:
        chunks.append(
            {
                "chunk_id": venue["venue_key"],
                "venue_key": venue["venue_key"],
                "text": _venue_text(venue),
                "metadata": {
                    "canonical_name": venue["canonical_name"],
                    "aliases": venue.get("aliases", []),
                    "district": venue.get("district"),
                    "district_candidates": venue.get("district_candidates", []),
                    "addresses": venue.get("addresses", []),
                    "opening_hours": venue.get("opening_hours"),
                    "open_time": venue.get("open_time"),
                    "close_time": venue.get("close_time"),
                    "all_day": venue.get("all_day", False),
                    "overnight": venue.get("overnight", False),
                    "price_text": venue.get("price_text"),
                    "price_min_vnd": venue.get("price_min_vnd"),
                    "price_max_vnd": venue.get("price_max_vnd"),
                    "n_sources": venue.get("n_sources", 0),
                    "source_urls": sorted({
                        record["url"] for record in venue.get("source_records", []) if record.get("url")
                    }),
                    "images": venue.get("images", []),
                },
            }
        )
    return chunks


def run_chunk() -> dict:
    tagged = read_jsonl(TAGGED_DIR / "venues_tagged.jsonl")
    if not tagged:
        raise SystemExit("Chưa có data/05_tagged/venues_tagged.jsonl - hãy chạy tag trước.")
    chunks = build_chunks(tagged)
    CHUNKS_DIR.mkdir(parents=True, exist_ok=True)
    write_jsonl(CHUNKS_DIR / "chunks.jsonl", chunks)
    report = {
        "venues_in": len(tagged),
        "chunks_out": len(chunks),
        "multi_chunk_venues": 0,
        "chunk_strategy": "one venue per chunk",
    }
    (CHUNKS_DIR / "chunk_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
