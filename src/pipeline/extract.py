"""Bước 1c - EXTRACT: data/02_clean_html -> data/03_extracted/*.jsonl

Hai đầu ra:
  documents.jsonl : mỗi trang = 1 bản ghi (title, mô tả, ngày, các section theo heading, toàn văn)
  venues.jsonl    : mỗi phòng trà trong bài listing = 1 bản ghi (name, tagline, địa chỉ, mô tả)
                    -> đơn vị nhỏ, giàu ngữ nghĩa, rất hợp để chunk + embedding ở bước sau.
"""
from __future__ import annotations

import json
import logging
import re

from bs4 import BeautifulSoup

from .config import CLEAN_DIR, EXTRACT_DIR, RAW_DIR
from .utils import LINE_BREAK_TOKEN, content_hash, iter_manifest_documents, normalize_text, write_jsonl

log = logging.getLogger("extract")

TEXT_BLOCKS = ["h1", "h2", "h3", "h4", "li", "p", "blockquote"]
BLOCKS = TEXT_BLOCKS + ["img"]  # img đi chung dòng thời gian để biết ảnh thuộc section nào
HEADING_LEVEL = {"h1": 1, "h2": 2, "h3": 3, "h4": 4}
ADDR_RE = re.compile(r"^(?:Địa chỉ|Địa điểm|Add(?:ress)?)\s*:\s*(.*)$", re.I)
BRANCH_RE = re.compile(r"^(?:CN|Cơ sở|Chi nhánh)\s*\d*\s*:\s*(.+)$", re.I)
HOURS_RE = re.compile(r"^(?:Giờ mở cửa|Giờ hoạt động|Thời gian mở cửa|Mở cửa)\s*:\s*(.*)$", re.I)
PRICE_RE = re.compile(r"^(?:Giá(?: đồ uống)?(?: từ)?|Mức giá|Giá vé)\s*:\s*(.*)$", re.I)
MIN_DOC_CHARS = 200


def _text_lines(el) -> list[str]:
    return [line for raw in el.get_text().split(LINE_BREAK_TOKEN) if (line := normalize_text(raw))]


def _blocks(root):
    """Các khối văn bản 'lá' theo thứ tự xuất hiện (bỏ khối chỉ chứa khối khác để khỏi trùng lặp)."""
    for el in root.find_all(BLOCKS):
        if el.name != "img" and el.find(TEXT_BLOCKS):
            continue
        yield el


def build_sections(clean_html: str, src: dict) -> list[dict]:
    soup = BeautifulSoup(clean_html, "lxml")
    root = soup.find(id="main-content") or soup.body
    drop = [re.compile(p) for p in src.get("drop_text_patterns", [])]
    cut = [re.compile(p) for p in src.get("cut_after_patterns", [])]

    sections: list[dict] = []
    cur = {"heading": None, "level": 0, "paragraphs": [], "images": []}

    def flush():
        if cur["paragraphs"] or cur["images"]:
            sections.append({**cur, "paragraphs": list(cur["paragraphs"]), "images": list(cur["images"])})

    venue_heading_re = re.compile(src["venue_heading_regex"]) if src.get("venue_heading_regex") else None

    for el in _blocks(root):
        if el.name == "img":
            # ảnh thuộc về section (heading) đang mở, tức là quán đang được nhắc tới
            url = el.get("src")
            if url and all(i["src"] != url for i in cur["images"]):
                cur["images"].append({"src": url, "alt": el.get("alt", ""), "caption": el.get("data-caption", "")})
            continue
        lines = _text_lines(el)
        if el.name in HEADING_LEVEL:
            lines = [normalize_text(" ".join(lines))]
        block_lines = []
        for text in lines:
            text = re.sub(r"^[•\-–·▪]\s*", "", text)
            if not text:
                continue
            if any(p.search(text) for p in cut):
                flush()
                return sections
            if any(p.search(text) for p in drop):
                continue
            if el.name in HEADING_LEVEL:
                if el.name == "h1":
                    continue  # h1 = tiêu đề trang, lấy từ meta
                is_venue = venue_heading_re and venue_heading_re.match(cur["heading"] or "")
                if is_venue and HEADING_LEVEL[el.name] > cur["level"]:
                    if not cur["paragraphs"] or cur["paragraphs"][-1] != text:
                        cur["paragraphs"].append(text)
                    continue
                flush()
                cur = {"heading": text, "level": HEADING_LEVEL[el.name], "paragraphs": [], "images": []}
            else:
                block_lines.append(text)
        if block_lines:
            text = "\n".join(block_lines)
            if not cur["paragraphs"] or cur["paragraphs"][-1] != text:
                cur["paragraphs"].append(text)
    flush()
    return sections


def parse_venues(sections: list[dict], src: dict, doc: dict) -> list[dict]:
    pattern = src.get("venue_heading_regex")
    if pattern:
        heading_re = re.compile(pattern)
        venue_sections = sections
    elif src.get("venue_name"):
        paragraphs = []
        for section in sections:
            if section.get("heading"):
                paragraphs.append(section["heading"])
            paragraphs.extend(section.get("paragraphs", []))
        venue_sections = [{
            "heading": f"1. {src['venue_name']}",
            "level": 2,
            "paragraphs": paragraphs,
            "images": [],
        }]
        heading_re = re.compile(r"^\s*(\d{1,2})\.\s*(.+)$")
    else:
        return []
    venues = []
    for sec in venue_sections:
        m = heading_re.match(sec["heading"] or "")
        if not m or sec["level"] not in (2, 3):
            continue
        rank, rest = int(m.group(1)), m.group(2).strip()
        parts = re.split(r"\s+[-–—]\s+", rest, maxsplit=1)
        name = parts[0].strip()
        tagline = parts[1].strip() if len(parts) > 1 else ""

        addresses, body = [], []
        hours, price = None, None
        for paragraph in sec["paragraphs"]:
            pending_field = None
            lines = [normalize_text(line) for line in paragraph.splitlines()]
            lines = [line for line in lines if line]
            index = 0
            while index < len(lines):
                p = lines[index]
                index += 1
                if not p:
                    continue
                if pending_field:
                    if any(pattern.match(p) for pattern in (ADDR_RE, BRANCH_RE, HOURS_RE, PRICE_RE)):
                        pending_field = None
                    else:
                        if pending_field == "address":
                            addresses.append(p)
                        elif pending_field == "hours":
                            hours = p
                        elif pending_field == "price":
                            price = p
                        pending_field = None
                        continue
                a = ADDR_RE.match(p)
                b = BRANCH_RE.match(p)
                h = HOURS_RE.match(p)
                pr = PRICE_RE.match(p)
                if h:
                    hours = h.group(1).strip() or None
                    pending_field = "hours" if not hours else None
                elif pr:
                    price = pr.group(1).strip() or None
                    pending_field = "price" if not price else None
                elif a:
                    address = a.group(1).strip()
                    if address:
                        address_parts = [address]
                        while index < len(lines) and not any(
                            pattern.match(lines[index]) for pattern in (ADDR_RE, BRANCH_RE, HOURS_RE, PRICE_RE)
                        ):
                            address_parts.append(lines[index])
                            index += 1
                        addresses.append(" ".join(address_parts))
                    else:
                        pending_field = "address"
                elif b:
                    addresses.append(b.group(0).strip())
                else:
                    body.append(p)

        if not addresses and src.get("venue_name"):
            addresses = list(src.get("venue_addresses", []))

        if not addresses:
            log.warning("Không trích xuất được địa chỉ cho venue %r trong %s", name, doc.get("url"))

        text_parts = [f"{name}. {tagline}".strip(". ").strip() + "."]
        if addresses:
            text_parts.append("Địa chỉ: " + "; ".join(addresses))
        if hours:
            text_parts.append("Giờ mở cửa: " + hours)
        if price:
            text_parts.append("Giá: " + price)
        text_parts.extend(body)
        venues.append(
            {
                "venue_id": f"{doc['doc_id']}#{rank:02d}",
                "doc_id": doc["doc_id"],
                "source": doc["source"],
                "url": doc["url"],
                "rank_in_article": rank,
                "name": name,
                "tagline": tagline,
                "addresses": addresses,
                "opening_hours": hours,
                "price_text": price,
                "images": [dict(i) for i in sec.get("images", [])],
                "description": "\n".join(body),
                "text": "\n".join(text_parts),
                "published_at": doc["published_at"],
                "fetched_at": doc["fetched_at"],
            }
        )
    return venues


def extract_document(clean_html: str, meta: dict, src: dict, doc_id: str, source: str) -> tuple[dict, list[dict]]:
    sections = build_sections(clean_html, src)
    out_sections = [
        {
            "heading": s["heading"],
            "level": s["level"],
            "text": "\n".join(s["paragraphs"]),
            "images": [dict(i) for i in s["images"]],
        }
        for s in sections
    ]
    full = [meta["title"]]
    for s in out_sections:
        full.append((s["heading"] + "\n" if s["heading"] else "") + s["text"])
    text = "\n\n".join(x for x in full if x)

    doc = {
        "doc_id": doc_id,
        "source": source,
        "url": meta["url"],
        "title": meta["title"],
        "description": meta.get("description", ""),
        "published_at": meta.get("published_at"),
        "fetched_at": meta["fetched_at"],
        "cover_image": {"src": meta["og_image"], "alt": "", "caption": ""} if meta.get("og_image") else None,
        "language": "vi",
        "sections": out_sections,
        "text": text,
        "n_chars": len(text),
        "content_hash": content_hash(text),
    }
    return doc, parse_venues(sections, src, doc)


def extract_source(src: dict) -> tuple[list[dict], list[dict], dict]:
    name = src["name"]
    docs, venues = [], []
    stats = {"documents": 0, "venues": 0, "too_short": 0}
    for row in iter_manifest_documents(RAW_DIR, name):
        clean_path = CLEAN_DIR / name / f"{row['id']}.html"
        meta_path = CLEAN_DIR / name / f"{row['id']}.meta.json"
        if not clean_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        doc, v = extract_document(clean_path.read_text(encoding="utf-8"), meta, src, row["id"], name)
        if doc["n_chars"] < MIN_DOC_CHARS:
            log.warning("Bỏ qua (quá ngắn %d ký tự): %s", doc["n_chars"], doc["url"])
            stats["too_short"] += 1
            continue
        docs.append(doc)
        venues.extend(v)
    stats["documents"], stats["venues"] = len(docs), len(venues)
    return docs, venues, stats


def run_extract(sources: list[dict]) -> dict:
    all_docs, all_venues, report = [], [], {}
    for src in sources:
        docs, venues, stats = extract_source(src)
        all_docs += docs
        all_venues += venues
        report[src["name"]] = stats

    # khử trùng lặp nội dung giữa các nguồn/URL
    seen, unique = set(), []
    for d in all_docs:
        if d["content_hash"] in seen:
            log.info("Trùng nội dung, bỏ: %s", d["url"])
            continue
        seen.add(d["content_hash"])
        unique.append(d)
    keep = {d["doc_id"] for d in unique}
    all_venues = [v for v in all_venues if v["doc_id"] in keep]

    write_jsonl(EXTRACT_DIR / "documents.jsonl", unique)
    write_jsonl(EXTRACT_DIR / "venues.jsonl", all_venues)
    report["_total"] = {"documents": len(unique), "venues": len(all_venues), "duplicates_removed": len(all_docs) - len(unique)}
    (EXTRACT_DIR / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
