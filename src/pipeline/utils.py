from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path
from typing import Iterable, Iterator
from urllib.parse import urldefrag

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍﻿­"), None)
LINE_BREAK_TOKEN = "\ue000"


def normalize_url(url: str) -> str:
    """Bỏ fragment (#...) và khoảng trắng thừa."""
    return urldefrag(url.strip())[0]


def url_id(url: str) -> str:
    """ID ổn định 16 ký tự cho 1 URL (dùng làm tên file và doc_id)."""
    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:16]


def normalize_text(s: str) -> str:
    """Chuẩn hoá Unicode (NFC - quan trọng với tiếng Việt), bỏ ký tự vô hình, gộp khoảng trắng."""
    s = unicodedata.normalize("NFC", s).translate(_ZERO_WIDTH).replace("\xa0", " ")
    return re.sub(r"\s+", " ", s).strip()


def content_hash(text: str) -> str:
    return hashlib.sha1(normalize_text(text).lower().encode("utf-8")).hexdigest()


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def iter_manifest_documents(raw_dir: Path, source_name: str) -> Iterator[dict]:
    """Lấy bản ghi fetch thành công mới nhất cho mỗi URL, bỏ qua lần fetch thất bại sau đó."""
    latest: dict[str, dict] = {}
    for row in read_jsonl(raw_dir / source_name / "manifest.jsonl"):
        if row.get("role") == "document" and row.get("status") == 200:
            latest[row["url"]] = row
    for row in latest.values():
        yield row
