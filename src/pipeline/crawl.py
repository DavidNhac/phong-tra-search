"""Bước 1a - CRAWL: tải HTML gốc về data/01_raw_html/<source>/.

- Lịch sự: kiểm tra robots.txt, nghỉ giữa các request, User-Agent rõ ràng.
- Chạy lại được (resume): URL đã tải thành công sẽ được bỏ qua, trừ khi --force.
- listing_seeds chỉ dùng để dò link bài viết; seeds mới là trang đi vào dữ liệu.
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from urllib import robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from .config import RAW_DIR
from .utils import append_jsonl, normalize_url, read_jsonl, url_id

log = logging.getLogger("crawl")


class Fetcher:
    def __init__(self, defaults: dict):
        self.delay = float(defaults.get("delay_seconds", 2.0))
        self.timeout = float(defaults.get("timeout_seconds", 20))
        self.max_retries = int(defaults.get("max_retries", 3))
        self.respect_robots = bool(defaults.get("respect_robots_txt", True))
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": defaults.get("user_agent", "PhongTraSearchBot/0.1"),
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "vi,en;q=0.8",
            }
        )
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, robotparser.RobotFileParser | None] = {}

    def _robots_for(self, url: str):
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._robots:
            rp = robotparser.RobotFileParser()
            try:
                r = self.session.get(f"{base}/robots.txt", timeout=self.timeout)
                if r.status_code == 200:
                    rp.parse(r.text.splitlines())
                    self._robots[base] = rp
                else:
                    self._robots[base] = None  # không có robots.txt => cho phép
            except requests.RequestException as e:
                log.warning("Không đọc được robots.txt của %s (%s) - tạm cho phép", base, e)
                self._robots[base] = None
        return self._robots[base]

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        rp = self._robots_for(url)
        return True if rp is None else rp.can_fetch(self.session.headers["User-Agent"], url)

    def _throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        wait = self.delay - (time.monotonic() - self._last_hit.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def get(self, url: str) -> tuple[int, str | None, str | None]:
        """Trả về (status, html, error). Retry với exponential backoff cho lỗi tạm thời."""
        last_err = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle(url)
            try:
                r = self.session.get(url, timeout=self.timeout)
                if r.status_code in (429, 500, 502, 503, 504):
                    last_err = f"HTTP {r.status_code}"
                    time.sleep(2**attempt)
                    continue
                ctype = r.headers.get("Content-Type", "")
                if r.status_code == 200 and "html" not in ctype.lower():
                    return r.status_code, None, f"không phải HTML ({ctype})"
                if not r.encoding or r.encoding.lower() == "iso-8859-1":
                    r.encoding = r.apparent_encoding or "utf-8"
                return r.status_code, r.text if r.status_code == 200 else None, None
            except requests.RequestException as e:
                last_err = str(e)
                time.sleep(2**attempt)
        return 0, None, last_err

    def get_binary(
        self, url: str, referer: str | None = None, max_bytes: int = 10_000_000
    ) -> tuple[int, bytes | None, str | None, str | None]:
        """Tải file nhị phân (ảnh). Trả về (status, bytes, content_type, error)."""
        if not self.allowed(url):
            return 0, None, None, "robots.txt chặn"
        headers = {"Accept": "image/avif,image/webp,image/*;q=0.8,*/*;q=0.5"}
        if referer:
            headers["Referer"] = referer  # nhiều CDN từ chối ảnh nếu thiếu Referer
        last_err = None
        for attempt in range(1, self.max_retries + 1):
            self._throttle(url)
            try:
                with self.session.get(url, timeout=self.timeout, headers=headers, stream=True) as r:
                    ctype = r.headers.get("Content-Type")
                    if r.status_code in (429, 500, 502, 503, 504):
                        last_err = f"HTTP {r.status_code}"
                        time.sleep(2**attempt)
                        continue
                    if r.status_code != 200:
                        return r.status_code, None, ctype, f"HTTP {r.status_code}"
                    clen = r.headers.get("Content-Length", "")
                    if clen.isdigit() and int(clen) > max_bytes:
                        return 200, None, ctype, f"quá lớn ({clen} bytes)"
                    buf = bytearray()
                    for chunk in r.iter_content(65536):
                        buf += chunk
                        if len(buf) > max_bytes:
                            return 200, None, ctype, "quá lớn"
                    return 200, bytes(buf), ctype, None
            except requests.RequestException as e:
                last_err = str(e)
                time.sleep(2**attempt)
        return 0, None, None, last_err


def discover_links(html: str, base_url: str, src: dict) -> list[dict]:
    """Từ 1 trang listing, tìm link bài viết khớp cấu hình. Trả về cả link bị loại để dễ kiểm tra."""
    soup = BeautifulSoup(html, "lxml")
    allowed = set(src.get("allowed_domains", []))
    art_re = re.compile(src["article_url_regex"]) if src.get("article_url_regex") else None
    inc_url = [p.lower() for p in src.get("link_include_patterns", [])]
    inc_text = [re.compile(p) for p in src.get("link_include_text_patterns", [])]
    exc = [re.compile(p) for p in src.get("link_exclude_patterns", [])]

    seen, out = set(), []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.lower().startswith(("javascript:", "mailto:", "tel:", "#")):
            continue
        url = normalize_url(urljoin(base_url, href))
        if url in seen:
            continue
        seen.add(url)
        if urlparse(url).netloc not in allowed:
            continue
        text = a.get_text(" ", strip=True)
        reason = None
        if art_re and not art_re.match(url):
            reason = "không khớp article_url_regex"
        elif any(p.search(url) for p in exc):
            reason = "khớp link_exclude"
        elif not (any(p in url.lower() for p in inc_url) or any(p.search(text) for p in inc_text)):
            reason = "không khớp từ khoá"
        out.append({"url": url, "anchor": text[:120], "matched": reason is None, "reason": reason})
    return out


def crawl_source(src: dict, defaults: dict, force: bool = False) -> dict:
    name = src["name"]
    out_dir = RAW_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = out_dir / "manifest.jsonl"

    done = {r["url"]: r for r in read_jsonl(manifest) if r.get("status") == 200}
    fetcher = Fetcher(defaults)
    allowed_domains = set(src.get("allowed_domains", []))
    max_docs = src.get("max_pages")

    queue: list[tuple[str, str]] = [(u, "listing") for u in src.get("listing_seeds", [])]
    queue += [(u, "document") for u in src.get("seeds", [])]

    seen: set[str] = set()
    stats = {"fetched": 0, "skipped_cached": 0, "failed": 0, "blocked_robots": 0, "documents": 0}
    audit: list[dict] = []

    while queue:
        url, role = queue.pop(0)
        url = normalize_url(url)
        if url in seen:
            continue
        seen.add(url)
        if urlparse(url).netloc not in allowed_domains:
            continue
        if role == "document" and max_docs and stats["documents"] >= max_docs:
            continue

        raw_path = out_dir / f"{url_id(url)}.html"
        html = None
        if url in done and not force and raw_path.exists():
            html = raw_path.read_text(encoding="utf-8")
            stats["skipped_cached"] += 1
        else:
            if not fetcher.allowed(url):
                log.warning("robots.txt chặn: %s", url)
                stats["blocked_robots"] += 1
                continue
            status, html, err = fetcher.get(url)
            row = {
                "url": url,
                "id": url_id(url),
                "role": role,
                "status": status,
                "error": err,
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "path": str(raw_path.relative_to(RAW_DIR.parent)) if html else None,
            }
            append_jsonl(manifest, row)
            if html is None:
                log.error("Lỗi %s: status=%s %s", url, status, err)
                stats["failed"] += 1
                continue
            raw_path.write_text(html, encoding="utf-8")
            stats["fetched"] += 1
            log.info("OK [%s] %s", role, url)

        if role == "document":
            stats["documents"] += 1
        elif src.get("discover_links") and html:
            links = discover_links(html, url, src)
            audit.extend({"from": url, **l} for l in links)
            for l in links:
                if l["matched"]:
                    queue.append((l["url"], "document"))

    if audit:  # để bạn kiểm tra link nào được/không được chọn
        (out_dir / "discovered_links.json").write_text(
            json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return stats
