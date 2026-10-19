"""Bước 1d - IMAGES: tải ảnh của từng venue/document về data/03_extracted/images/<source>/.

Đọc venues.jsonl và documents.jsonl (do extract tạo ra), tải các ảnh được liệt kê trong trường
`images` / `cover_image`, rồi GHI LẠI hai file đó với thêm thông tin cho mỗi ảnh:
  status      : ok | skipped_pattern | skipped_format | skipped_small | skipped_limit | skipped_undecodable | failed
  local_path  : đường dẫn tương đối so với thư mục data/ (chỉ có khi status = ok)
  width/height/bytes/sha1/content_type

Ảnh đặt tên theo sha1 của nội dung nên cùng 1 ảnh xuất hiện ở nhiều nơi chỉ lưu 1 file.
Kết quả mỗi URL được nhớ trong images/manifest.json, chạy lại sẽ không tải lại (trừ khi --force).
Chỉnh ngưỡng lọc trong sources.yaml, khối `images:` ở `defaults` hoặc ở từng source (đều không bắt buộc).
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from collections import Counter

from .config import DATA_DIR, EXTRACT_DIR, IMAGES_DIR
from .crawl import Fetcher
from .utils import read_jsonl, write_jsonl

try:  # Pillow để đọc kích thước ảnh; không có thì bỏ qua lọc theo kích thước
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

log = logging.getLogger("images")

DEFAULTS = {
    "enabled": True,
    "download_cover": True,      # ảnh og:image đại diện của bài
    "max_per_venue": 5,
    "min_bytes": 8_000,
    "max_bytes": 10_000_000,
    "min_width": 300,
    "min_height": 200,
    "delay_seconds": 0.5,        # nghỉ giữa 2 ảnh cùng host
    # URL khớp 1 trong các regex này bị bỏ (logo, icon, banner quảng cáo...)
    "skip_url_patterns": [
        r"(?i)(logo|favicon|sprite|avatar|placeholder|spinner|quangcao)",
        r"(?i)/weblink/",
        r"(?i)[-_/]icons?[-_./0-9]",
        r"(?i)[-_/]banner[-_./0-9]",
    ],
}
EXT = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png", "image/webp": ".webp", "image/avif": ".avif"}


def resolve_cfg(defaults_cfg: dict, src_cfg: dict) -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(defaults_cfg.get("images") or {})
    cfg.update(src_cfg.get("images") or {})
    return cfg


def fetch_one(url: str, referer: str, cfg: dict, fetcher, images_dir, rel_root) -> dict:
    """Tải + kiểm tra 1 ảnh. Luôn trả về dict có khoá 'status'."""
    if any(re.search(p, url) for p in cfg["skip_url_patterns"]):
        return {"status": "skipped_pattern"}
    if url.lower().split("?")[0].endswith((".svg", ".gif")):
        return {"status": "skipped_format"}

    status, content, ctype, err = fetcher.get_binary(url, referer, cfg["max_bytes"])
    if content is None:
        return {"status": "failed", "error": err or f"HTTP {status}"}
    ctype = (ctype or "").split(";")[0].strip().lower()
    if not ctype.startswith("image/") or "svg" in ctype or "gif" in ctype:
        return {"status": "skipped_format", "content_type": ctype}
    if len(content) < cfg["min_bytes"]:
        return {"status": "skipped_small", "bytes": len(content)}

    w = h = None
    if Image is not None:
        try:
            with Image.open(io.BytesIO(content)) as im:
                w, h = im.size
        except Exception:
            if ctype != "image/avif":  # Pillow mặc định không đọc được avif, các loại khác mà lỗi là file hỏng
                return {"status": "skipped_undecodable", "content_type": ctype}
    if w and (w < cfg["min_width"] or h < cfg["min_height"]):
        return {"status": "skipped_small", "width": w, "height": h, "bytes": len(content)}

    sha = hashlib.sha1(content).hexdigest()
    path = images_dir / f"{sha[:16]}{EXT.get(ctype, '.img')}"
    if not path.exists():
        images_dir.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    return {
        "status": "ok",
        "local_path": path.relative_to(rel_root).as_posix(),
        "width": w,
        "height": h,
        "bytes": len(content),
        "sha1": sha,
        "content_type": ctype,
    }


def enrich(venues, docs, defaults_cfg, sources_cfg, fetcher, images_root, rel_root, cache, only=None) -> Counter:
    """Gắn kết quả tải vào từng ảnh (sửa trực tiếp venues/docs). `cache`: url -> kết quả, được cập nhật."""
    stats: Counter = Counter()

    def cfg_for(source: str) -> dict:
        return resolve_cfg(defaults_cfg, sources_cfg.get(source, {}))

    def process(item: dict, source: str, referer: str, cfg: dict) -> dict:
        url = item["src"]
        res = cache.get(url)
        if res is not None and res["status"] == "failed":
            res = None  # lỗi thì thử lại
        if res is not None and res["status"] == "ok" and not (rel_root / res["local_path"]).exists():
            res = None  # file đã bị xoá thì tải lại
        if res is None:
            res = fetch_one(url, referer, cfg, fetcher, images_root / source, rel_root)
            cache[url] = res
            log.info("[%s] %s  %s", res["status"], url, res.get("error", ""))
        item.update(res)
        stats[res["status"]] += 1
        return res

    for v in venues:
        if only is not None and v["source"] not in only:
            continue
        cfg = cfg_for(v["source"])
        if not cfg["enabled"]:
            continue
        ok = 0
        for it in v.get("images", []):
            if ok >= cfg["max_per_venue"]:
                it["status"] = "skipped_limit"
                stats["skipped_limit"] += 1
                continue
            ok += process(it, v["source"], v["url"], cfg)["status"] == "ok"

    source_venue_images = {}
    for v in venues:
        if only is not None and v["source"] not in only:
            continue
        for image in v.get("images", []):
            source_venue_images[(v["source"], image["src"])] = {
                key: value for key, value in image.items() if key != "src"
            }

    sources_with_venues = {v["source"] for v in venues}
    for d in docs:
        if only is not None and d["source"] not in only:
            continue
        cfg = cfg_for(d["source"])
        if not cfg["enabled"]:
            continue
        if d.get("cover_image") and cfg["download_cover"]:
            process(d["cover_image"], d["source"], d["url"], cfg)
        for s in d.get("sections", []):
            for it in s.get("images", []):
                result = source_venue_images.get((d["source"], it["src"]))
                if result is not None:
                    it.update(result)
                elif d["source"] not in sources_with_venues:
                    process(it, d["source"], d["url"], cfg)
                else:
                    for field in ("status", "error", "local_path", "width", "height", "bytes", "sha1", "content_type"):
                        it.pop(field, None)
    return stats


def run_images(sources: list[dict], defaults: dict, force: bool = False) -> dict:
    venues = read_jsonl(EXTRACT_DIR / "venues.jsonl")
    docs = read_jsonl(EXTRACT_DIR / "documents.jsonl")
    if not docs:
        raise SystemExit("Chưa có data/03_extracted/documents.jsonl - hãy chạy bước extract trước.")
    if Image is None:
        log.warning("Chưa cài Pillow (pip install Pillow): sẽ không lọc được ảnh theo kích thước.")

    manifest_path = IMAGES_DIR / "manifest.json"
    cache = {}
    if manifest_path.exists() and not force:
        cache = json.loads(manifest_path.read_text(encoding="utf-8"))

    sources_cfg = {s["name"]: s for s in sources}
    only = set(sources_cfg)
    fetcher = Fetcher({**defaults, "delay_seconds": resolve_cfg(defaults, {})["delay_seconds"]})

    stats = enrich(venues, docs, defaults, sources_cfg, fetcher, IMAGES_DIR, DATA_DIR, cache, only)

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    write_jsonl(EXTRACT_DIR / "venues.jsonl", venues)
    write_jsonl(EXTRACT_DIR / "documents.jsonl", docs)

    per_venue = {v["venue_id"]: sum(1 for i in v.get("images", []) if i.get("status") == "ok") for v in venues}
    report = {
        "by_status": dict(stats),
        "venues_with_image": sum(1 for n in per_venue.values() if n),
        "venues_total": len(venues),
        "venues_without_image": [v["name"] for v in venues if not per_venue[v["venue_id"]]],
    }
    (IMAGES_DIR / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
