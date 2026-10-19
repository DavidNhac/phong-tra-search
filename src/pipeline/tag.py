"""Normalize district, opening-hours, and price metadata for merged venues."""
from __future__ import annotations

import json
import re
import unicodedata

from .config import MERGED_DIR, TAGGED_DIR
from .utils import normalize_text, read_jsonl, write_jsonl

DISTRICTS = (
    "Ba Đình",
    "Bắc Từ Liêm",
    "Cầu Giấy",
    "Đống Đa",
    "Gia Lâm",
    "Hà Đông",
    "Hai Bà Trưng",
    "Hoàn Kiếm",
    "Hoàng Mai",
    "Long Biên",
    "Nam Từ Liêm",
    "Tây Hồ",
    "Thanh Xuân",
)


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return "".join(char for char in text if unicodedata.category(char) != "Mn").casefold()


DISTRICT_PATTERNS = [
    (district, re.compile(rf"(?:\bq\.?\s*|\bquan\s+){re.escape(_fold(district))}\b"))
    for district in DISTRICTS
]
HOUR_RANGE_RE = re.compile(
    r"(?i)(?<!\d)(\d{1,2})\s*h\s*(\d{0,2})\s*[-–—]\s*(\d{1,2})\s*h\s*(\d{0,2})(?!\d)"
)
PRICE_AMOUNT_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[.,]\d{3})+|\d+)(?!\d)")
PRICE_CONTEXT_RE = re.compile(r"(?i)(?:giá|mức giá)[^\n]{0,100}")
SENTENCE_END_RE = re.compile(r"(?<!\d)[.!?](?=\s|$)")


def _district_candidates(venue: dict) -> list[str]:
    found = set()
    addresses = [item["address"] for item in venue.get("source_addresses", [])]
    addresses += venue.get("addresses", [])
    for address in addresses:
        folded = _fold(normalize_text(address))
        for district, pattern in DISTRICT_PATTERNS:
            if pattern.search(folded):
                found.add(district)
    return sorted(found)


def _parse_clock(hour: str, minute: str) -> str | None:
    h, m = int(hour), int(minute or 0)
    if not 0 <= h <= 23 or not 0 <= m <= 59:
        return None
    return f"{h:02d}:{m:02d}"


def _opening_metadata(value: str | None) -> dict:
    if not value:
        return {
            "open_time": None,
            "close_time": None,
            "all_day": False,
            "overnight": False,
            "opening_hours_status": "not_provided",
        }
    text = normalize_text(value)
    folded = text.casefold()
    if "cả ngày" in folded or "ca ngay" in folded:
        return {
            "open_time": None,
            "close_time": None,
            "all_day": True,
            "overnight": False,
            "opening_hours_status": "parsed",
        }
    match = HOUR_RANGE_RE.search(text)
    if not match:
        return {
            "open_time": None,
            "close_time": None,
            "all_day": False,
            "overnight": False,
            "opening_hours_status": "unparsed",
        }
    opening = _parse_clock(match.group(1), match.group(2))
    closing = _parse_clock(match.group(3), match.group(4))
    if opening is None or closing is None:
        return {
            "open_time": None,
            "close_time": None,
            "all_day": False,
            "overnight": False,
            "opening_hours_status": "unparsed",
        }
    return {
        "open_time": opening,
        "close_time": closing,
        "all_day": False,
        "overnight": closing <= opening,
        "opening_hours_status": "parsed",
    }


def _amounts(text: str) -> list[int]:
    values = []
    for match in PRICE_AMOUNT_RE.finditer(text):
        digits = re.sub(r"[.,]", "", match.group(1))
        try:
            amount = int(digits)
        except ValueError:
            continue
        if amount >= 1_000:
            values.append(amount)
    return values


def _price_metadata(value: str | None) -> dict:
    if not value:
        return {
            "price_min_vnd": None,
            "price_max_vnd": None,
            "price_currency": None,
            "price_status": "not_provided",
        }
    amounts = _amounts(value)
    if not amounts:
        return {
            "price_min_vnd": None,
            "price_max_vnd": None,
            "price_currency": None,
            "price_status": "unparsed",
        }
    return {
        "price_min_vnd": min(amounts),
        "price_max_vnd": max(amounts) if len(amounts) > 1 else None,
        "price_currency": "VND",
        "price_status": "parsed",
    }


def _price_observations(venue: dict) -> list[dict]:
    observations = []
    for record in venue.get("source_records", []):
        description = record.get("description") or ""
        for match in PRICE_CONTEXT_RE.finditer(description):
            text = match.group()
            sentence_end = SENTENCE_END_RE.search(text)
            if sentence_end:
                text = text[:sentence_end.start()]
            amounts = _amounts(text)
            if not amounts:
                continue
            observations.append(
                {
                    "source": record["source"],
                    "text": normalize_text(text),
                    "amounts_vnd": amounts,
                }
            )
    return observations


def tag_venue(venue: dict) -> dict:
    tagged = dict(venue)
    districts = _district_candidates(venue)
    tagged["district"] = districts[0] if len(districts) == 1 else None
    tagged["district_candidates"] = districts
    tagged.update(_opening_metadata(venue.get("opening_hours")))
    tagged.update(_price_metadata(venue.get("price_text")))
    tagged["opening_hours_sources"] = [
        record["source"]
        for record in venue.get("source_records", [])
        if record.get("opening_hours")
    ]
    tagged["price_sources"] = [
        record["source"]
        for record in venue.get("source_records", [])
        if record.get("price_text")
    ]
    tagged["price_observations"] = _price_observations(venue)
    return tagged


def run_tag() -> dict:
    merged = read_jsonl(MERGED_DIR / "venues_merged.jsonl")
    if not merged:
        raise SystemExit("Chưa có data/04_merged/venues_merged.jsonl - hãy chạy merge trước.")
    tagged = [tag_venue(venue) for venue in merged]
    TAGGED_DIR.mkdir(parents=True, exist_ok=True)
    write_jsonl(TAGGED_DIR / "venues_tagged.jsonl", tagged)
    report = {
        "venues": len(tagged),
        "venues_with_district": sum(bool(v["district"]) for v in tagged),
        "venues_with_structured_hours": sum(v["opening_hours_status"] == "parsed" for v in tagged),
        "venues_with_structured_price": sum(v["price_status"] == "parsed" for v in tagged),
        "district_conflicts": [
            {"venue_key": v["venue_key"], "district_candidates": v["district_candidates"]}
            for v in tagged if len(v["district_candidates"]) > 1
        ],
    }
    (TAGGED_DIR / "tag_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
