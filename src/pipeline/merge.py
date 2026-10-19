"""Bước 2 - MERGE: chuẩn hoá tên và gộp quán trùng giữa các nguồn.

  data/03_extracted/venues.jsonl  ->  data/04_merged/venues_merged.jsonl  (+ merge_report.json)

Mỗi quán thật chỉ còn MỘT bản ghi, gom thông tin của mọi nguồn nhắc tới nó:
  - tên chuẩn + các tên gọi khác (aliases), địa chỉ (hợp nhất, bỏ bản trùng), giờ mở cửa, giá
  - ảnh đã tải (bỏ ảnh trùng theo sha1)
  - source_records: giữ nguyên bản ghi gốc của từng nguồn (mô tả, url...) để truy vết và để chunking dùng sau

Nguyên tắc: ưu tiên ĐỘ CHÍNH XÁC hơn là gộp bằng được. Chỉ gộp khi tên chuẩn trùng khớp VÀ địa chỉ không
mâu thuẫn. Các ca nghi ngờ không được tự gộp mà ghi vào merge_report.json để bạn xem rồi chỉnh
config/venue_aliases.yaml.
"""
from __future__ import annotations

import difflib
import json
import logging
import re
import unicodedata
from collections import defaultdict
from itertools import combinations
from pathlib import Path

import yaml

from .config import ALIASES_PATH, EXTRACT_DIR, MERGED_DIR
from .utils import normalize_text, read_jsonl, write_jsonl

log = logging.getLogger("merge")

QUOTE_CHARS = re.compile(r"[‘’“”\"`«»]")
STRIP_CHARS = " '.,;:-–—"
LEAD_A = re.compile(r"^(?:phòng trà|quán cafe|quán cà phê|cafe|cà phê)\s+", re.I)
LEAD_B = re.compile(r"^(?:(?:ca nhạc|nhạc sống)\s+)?(?:(?:tại|ở)\s+)?hà nội\s+(?:nổi tiếng\s+)?|^nổi tiếng\s+", re.I)
TAIL = re.compile(r"\s+(?:(?:trên|tại|ở)\s+(?:đường|phố)\s+.+|hà nội)$", re.I)
ADDR_STOP = {  # từ chỉ loại đơn vị, không giúp phân biệt địa chỉ
    "so", "ngo", "ngach", "hem", "pho", "duong", "phuong", "quan", "huyen", "xa", "tp", "thanh", "tang", "toa", "nha",
}
HOURS_RE = re.compile(r"(\d{1,2})\s*h\s*(\d{2})?\s*[-–—]\s*(\d{1,2})\s*h\s*(\d{2})?")
EMBEDDING_FIELD_LINES = {
    "addresses": re.compile(r"(?im)^\s*(?:địa chỉ|address)\s*:\s*.*(?:\n|$)"),
    "opening_hours": re.compile(r"(?im)^\s*(?:giờ mở cửa|giờ|opening hours)\s*:\s*.*(?:\n|$)"),
    "price_text": re.compile(r"(?im)^\s*(?:giá(?: đồ uống)?(?: từ)?|mức giá|giá vé)\s*:\s*.*(?:\n|$)"),
}
OVERRIDABLE_FIELDS = {"addresses", "opening_hours", "price_text"}


# ---------------------------------------------------------------- chuẩn hoá văn bản
def fold(s: str) -> str:
    """Bỏ dấu tiếng Việt + hạ chữ thường, để so khớp."""
    s = unicodedata.normalize("NFD", s.replace("đ", "d").replace("Đ", "D"))
    return "".join(c for c in s if unicodedata.category(c) != "Mn").lower()


def slugify(s: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", fold(s)))


def _strip_if_ok(pattern: re.Pattern, s: str) -> str:
    """Áp pattern nhưng giữ nguyên nếu kết quả chỉ còn <2 từ (tránh làm hỏng tên như 'Hà Nội Phố')."""
    t = pattern.sub("", s, count=1).strip(STRIP_CHARS)
    return t if len(t.split()) >= 2 else s


def clean_name(raw: str, aliases: dict[str, str] | None = None) -> str:
    name = QUOTE_CHARS.sub("", normalize_text(raw)).strip(STRIP_CHARS)
    if aliases and fold(name) in aliases:
        return aliases[fold(name)]
    for pat in (LEAD_A, LEAD_B, TAIL):
        name = _strip_if_ok(pat, name)
    return name.strip(STRIP_CHARS)


def clean_hours(h: str | None) -> str | None:
    if not h:
        return None
    h = re.sub(r"\s*[–—]\s*", " - ", normalize_text(h))
    return re.sub(r"[\s.;,]+$", "", h) or None


def suspicious_hours(h: str | None) -> bool:
    """Đóng cửa trước 12h trưa (không qua đêm), vd '7h00 - 11h30' với một quán bar: nên đối chiếu lại bài gốc."""
    m = HOURS_RE.search(h or "")
    if not m:
        return False
    start = int(m[1]) + int(m[2] or 0) / 60
    end = int(m[3]) + int(m[4] or 0) / 60
    return start < end <= 12


ADMIN_SEG = re.compile(r"^(?:phuong\b|p\.\s*|quan\b|q\.\s*|huyen\b|xa\b|tp\b|thanh pho\b|ha noi\b|viet nam\b)")
BUILDING_SEG = re.compile(r"^(tang|toa|nha|lau|khu|tttm|trung tam)\b")


def street_segment(a: str) -> str:
    """Phần 'số nhà + đường/ngõ' của địa chỉ. Bỏ đoạn hành chính (phường/quận/Hà Nội) và đoạn
    tầng/tòa nhà vì hai nguồn hay ghi khác nhau; các đoạn còn lại ghép lại, nên
    'Số 38, ngõ 106, Hoàng Quốc Việt' và 'Số 38 ngõ 106 Hoàng Quốc Việt' ra cùng kết quả."""
    segs = [x.strip() for x in re.sub(r"\(.*?\)", " ", a).split(",") if x.strip()]
    keep = [x for x in segs if not ADMIN_SEG.match(fold(x)) and not BUILDING_SEG.match(fold(x))]
    if not keep:
        keep = segs[:1]
    return " ".join(keep)


def addr_tokens(a: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", fold(street_segment(a))) if t not in ADDR_STOP}


def addr_similar(a: str, b: str, threshold: float = 0.6) -> bool:
    ta, tb = addr_tokens(a), addr_tokens(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= threshold


def any_addr_similar(xs: list[str], ys: list[str]) -> bool:
    return any(addr_similar(a, b) for a in xs for b in ys)


# ---------------------------------------------------------------- gộp
def _prepare(rec: dict, aliases: dict[str, str]) -> dict:
    r = dict(rec)
    r["raw_name"] = normalize_text(rec["name"])
    r["clean_name"] = clean_name(rec["name"], aliases)
    r["slug"] = slugify(r["clean_name"])
    r["opening_hours"] = clean_hours(rec.get("opening_hours"))
    return r


def _compatible(cluster: list[dict], rec: dict, force: set[str]) -> bool:
    if any(m["source"] == rec["source"] for m in cluster):
        return False  # 1 bài không nhắc cùng 1 quán hai lần
    if rec["slug"] in force:
        return True
    have = [a for m in cluster for a in m.get("addresses", [])]
    mine = rec.get("addresses", [])
    return not have or not mine or any_addr_similar(have, mine)


def _merge_addresses(members: list[dict]) -> list[str]:
    out: list[str] = []
    for m in members:
        for a in m.get("addresses", []):
            if not any(addr_similar(a, k) for k in out):
                out.append(a)
    return out


def _merge_images(members: list[dict]) -> list[dict]:
    seen, out = set(), []
    for m in members:
        for i in m.get("images", []):
            if i.get("status") != "ok":
                continue
            key = i.get("sha1") or i["src"]
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "local_path": i["local_path"], "width": i.get("width"), "height": i.get("height"),
                    "alt": i.get("alt", ""), "caption": i.get("caption", ""), "src": i["src"], "source": m["source"],
                }
            )
    return out


def _first(members: list[dict], field: str):
    return next((m[field] for m in members if m.get(field)), None)


def _validated_overrides(overrides: dict | None, aliases: dict[str, str]) -> dict[str, dict]:
    if overrides is None:
        return {}
    if not isinstance(overrides, dict):
        raise ValueError("'overrides' trong venue_aliases.yaml phải là một object.")

    validated = {}
    for name, values in overrides.items():
        if not isinstance(name, str) or not isinstance(values, dict):
            raise ValueError("Mỗi override phải có dạng tên quán: {field: value}.")
        unknown = set(values) - OVERRIDABLE_FIELDS
        if unknown:
            raise ValueError(f"Trường override không được hỗ trợ cho {name!r}: {sorted(unknown)}")
        for field in ("opening_hours", "price_text"):
            if field in values and values[field] is not None and not isinstance(values[field], str):
                raise ValueError(f"Override {field} của {name!r} phải là chuỗi hoặc null.")
        if "addresses" in values and (
            not isinstance(values["addresses"], list)
            or any(not isinstance(address, str) for address in values["addresses"])
        ):
            raise ValueError(f"Override addresses của {name!r} phải là danh sách chuỗi.")
        validated[slugify(clean_name(name, aliases))] = dict(values)
    return validated


def _embedding_text(rec: dict, overridden_fields: set[str]) -> str:
    text = rec.get("text") or ""
    for field, pattern in EMBEDDING_FIELD_LINES.items():
        if field in overridden_fields:
            text = pattern.sub("", text)
    return text.strip()


def merge_venues(records: list[dict], source_order: list[str], aliases: dict[str, str] | None = None,
                 force_merge: list[str] | None = None, overrides: dict | None = None) -> tuple[list[dict], dict]:
    aliases = {fold(normalize_text(k)): v for k, v in (aliases or {}).items()}
    force = {slugify(clean_name(x, aliases)) for x in (force_merge or [])}
    overrides_by_slug = _validated_overrides(overrides, aliases)
    order = {s: i for i, s in enumerate(source_order)}
    recs = sorted(
        (_prepare(r, aliases) for r in records),
        key=lambda r: (order.get(r["source"], 99), r.get("rank_in_article", 0)),
    )

    report: dict = {"records_in": len(recs), "address_conflicts": [], "field_conflicts": [], "overrides_applied": []}

    # 1) gom theo slug tên, rồi tách theo địa chỉ
    by_slug: dict[str, list[dict]] = defaultdict(list)
    for r in recs:
        by_slug[r["slug"]].append(r)
    clusters: list[list[dict]] = []
    for slug, group in by_slug.items():
        local: list[list[dict]] = []
        for r in group:
            home = next((c for c in local if _compatible(c, r, force)), None)
            if home is not None:
                home.append(r)
                continue
            clash = next((c for c in local if all(m["source"] != r["source"] for m in c)), None)
            if clash is not None:  # cùng tên khác nguồn nhưng địa chỉ mâu thuẫn
                report["address_conflicts"].append(
                    {"slug": slug, "a": clash[0]["addresses"], "b": r["addresses"],
                     "hint": f"nếu là cùng một quán, thêm '{r['clean_name']}' vào force_merge"}
                )
            local.append([r])
        clusters += local

    # 2) dựng bản ghi gộp
    used: dict[str, int] = defaultdict(int)
    merged = []
    for members in clusters:
        slug = members[0]["slug"]
        used[slug] += 1
        key = slug if used[slug] == 1 else f"{slug}-{used[slug]}"
        best = min(members, key=lambda m: m["raw_name"] != m["clean_name"])  # ưu tiên tên không cần sửa
        names, seen_f = [], {fold(best["clean_name"])}
        for m in members:
            for n in (m["clean_name"], m["raw_name"]):
                if fold(n) not in seen_f:
                    seen_f.add(fold(n))
                    names.append(n)
        hours = _first(members, "opening_hours")
        price = _first(members, "price_text")
        for field in ("opening_hours", "price_text"):
            vals = {fold(m[field]) for m in members if m.get(field)}
            if len(vals) > 1:
                report["field_conflicts"].append(
                    {"venue_key": key, "field": field, "values": {m["source"]: m[field] for m in members if m.get(field)}}
                )
        manual_overrides = overrides_by_slug.get(slug, {})
        addresses = _merge_addresses(members)
        if "addresses" in manual_overrides:
            addresses = manual_overrides["addresses"]
        if "opening_hours" in manual_overrides:
            hours = manual_overrides["opening_hours"]
        if "price_text" in manual_overrides:
            price = manual_overrides["price_text"]
        if manual_overrides:
            report["overrides_applied"].append({"venue_key": key, "fields": sorted(manual_overrides)})
        merged.append(
            {
                "venue_key": key,
                "canonical_name": best["clean_name"],
                "aliases": names,
                "addresses": addresses,
                "source_addresses": [
                    {"address": address, "source": m["source"], "venue_id": m.get("venue_id")}
                    for m in members
                    for address in m.get("addresses", [])
                ],
                "opening_hours": hours,
                "price_text": price,
                "manual_overrides": manual_overrides,
                "taglines": sorted({m["tagline"] for m in members if m.get("tagline")}),
                "images": _merge_images(members),
                "published_at": max((m["published_at"] for m in members if m.get("published_at")), default=None),
                "n_sources": len({m["source"] for m in members}),
                "source_records": [
                    {
                        **{k: m.get(k) for k in (
                            "venue_id", "source", "url", "rank_in_article", "raw_name", "tagline", "addresses",
                            "opening_hours", "price_text", "description", "text", "fetched_at")},
                        "embedding_text": _embedding_text(m, set(manual_overrides)),
                    }
                    for m in members
                ],
            }
        )
    merged.sort(key=lambda v: (-v["n_sources"], v["canonical_name"]))

    # 3) các ca cần người xem lại
    report["venues_out"] = len(merged)
    report["merged_groups"] = [
        {"venue_key": v["venue_key"], "names": [s["raw_name"] for s in v["source_records"]]}
        for v in merged if v["n_sources"] > 1
    ]
    report["single_source"] = [v["canonical_name"] for v in merged if v["n_sources"] == 1]
    report["hours_to_review"] = [
        {"venue": v["canonical_name"], "hours": v["opening_hours"]} for v in merged if suspicious_hours(v["opening_hours"])
    ]
    possible, same_addr = [], []
    for a, b in combinations(merged, 2):
        ta, tb = set(a["venue_key"].split("-")), set(b["venue_key"].split("-"))
        sim = difflib.SequenceMatcher(None, a["venue_key"], b["venue_key"]).ratio()
        subset = (ta < tb and len(ta) >= 2) or (tb < ta and len(tb) >= 2)
        if sim >= 0.8 or subset:
            possible.append({"a": a["canonical_name"], "b": b["canonical_name"], "similarity": round(sim, 2)})
        elif any_addr_similar(a["addresses"], b["addresses"]):
            same_addr.append({"a": a["canonical_name"], "b": b["canonical_name"], "addresses": [a["addresses"], b["addresses"]]})
    report["possible_duplicates"] = possible
    report["same_address_different_name"] = same_addr
    return merged, report


def load_aliases(path: Path = ALIASES_PATH) -> tuple[dict, list, dict]:
    if not path.exists():
        return {}, [], {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("aliases") or {}, data.get("force_merge") or [], data.get("overrides") or {}


def run_merge(source_order: list[str], aliases_path: Path = ALIASES_PATH) -> dict:
    records = read_jsonl(EXTRACT_DIR / "venues.jsonl")
    if not records:
        raise SystemExit("Chưa có data/03_extracted/venues.jsonl - hãy chạy extract (và images) trước.")
    if any("status" not in i for r in records for i in r.get("images", [])):
        log.warning("Có ảnh chưa được tải (chưa chạy bước images?): ảnh sẽ không có trong kết quả gộp.")
    aliases, force, overrides = load_aliases(aliases_path)
    merged, report = merge_venues(records, source_order, aliases, force, overrides)

    MERGED_DIR.mkdir(parents=True, exist_ok=True)
    write_jsonl(MERGED_DIR / "venues_merged.jsonl", merged)
    (MERGED_DIR / "merge_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return {k: report[k] for k in (
        "records_in", "venues_out", "merged_groups", "address_conflicts", "possible_duplicates",
        "same_address_different_name", "hours_to_review", "field_conflicts", "overrides_applied")}
