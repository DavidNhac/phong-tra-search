"""Bước 1b - CLEAN DOM: data/01_raw_html -> data/02_clean_html.

Chiến lược 4 lớp, từ thô đến tinh:
  1. Xoá theo TAG (script, style, nav, footer, form, img...)
  2. Xoá theo SELECTOR cấu hình riêng từng site (banner, related, booking...)
  3. Chọn VÙNG NỘI DUNG: ưu tiên selector trong config, fallback heuristic mật độ văn bản
  4. Tỉa bên trong vùng đó: khối nhiều link (mục lục, "xem thêm"), bỏ thẻ <a> giữ chữ, bỏ thẻ rỗng

Hàm clean_html() là hàm thuần (không đụng file) nên dễ test.
"""
from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from pathlib import Path

from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Comment, Tag

from .config import CLEAN_DIR, RAW_DIR
from .utils import iter_manifest_documents, normalize_text

log = logging.getLogger("clean")

STRIP_TAGS = [
    "script", "style", "noscript", "iframe", "svg", "canvas", "template",
    "form", "button", "input", "select", "textarea", "label",
    "nav", "header", "footer", "aside",
    "video", "audio", "map", "object", "embed",   # img/picture được GIỮ lại và chuẩn hoá ở _normalize_images
]
HEADINGS = ["h1", "h2", "h3", "h4", "h5", "h6"]
MIN_CONTAINER_CHARS = 300
DATE_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b")


# ---------------------------------------------------------------- helpers
def _text_len(el: Tag) -> int:
    return len(normalize_text(el.get_text(" ")))


def _link_density(el: Tag) -> float:
    total = _text_len(el)
    if total == 0:
        return 0.0
    link = sum(len(normalize_text(a.get_text(" "))) for a in el.find_all("a"))
    return min(1.0, link / total)


def _decomposed(el: Tag) -> bool:
    return getattr(el, "decomposed", False)


IMG_LAZY_ATTRS = ("data-src", "data-lazy-src", "data-original", "data-lazy", "data-url")
IMG_SRCSET_ATTRS = ("data-srcset", "srcset")


def _fix_url(url: str, base_url: str = "") -> str:
    """Trả về URL http(s) tuyệt đối, hoặc '' nếu không dùng được."""
    url = (url or "").strip()
    if not url or url.startswith("data:"):
        return ""
    # vd luhanhvietnam trả og:image dạng "/tour-du-lichhttps://.../a.jpg": đường dẫn tương đối dính vào
    # trước URL thật. Chỉ cắt khi chuỗi KHÔNG bắt đầu bằng http (tránh phá URL hợp lệ có "?url=https://...").
    if not url.lower().startswith(("http://", "https://")):
        m = re.search(r"https?://", url)
        if m:
            url = url[m.start():]
    full = urljoin(base_url, url) if base_url else url
    return full if urlparse(full).scheme in ("http", "https") else ""


def _largest_from_srcset(srcset: str) -> str:
    best, best_w = "", -1.0
    for part in srcset.split(","):
        bits = part.strip().split()
        if not bits:
            continue
        w = 0.0
        if len(bits) > 1:
            try:
                w = float(bits[1].rstrip("wx"))
            except ValueError:
                pass
        if w >= best_w:
            best, best_w = bits[0], w
    return best


def _img_url(img: Tag, base_url: str) -> str:
    """Ưu tiên thuộc tính lazy-load (src thường chỉ là ảnh giữ chỗ), rồi srcset (bản lớn nhất), cuối cùng src."""
    cands = [img.get(a, "") for a in IMG_LAZY_ATTRS]
    cands += [_largest_from_srcset(img.get(a, "")) for a in IMG_SRCSET_ATTRS if img.get(a)]
    cands.append(img.get("src", ""))
    for c in cands:
        url = _fix_url(c, base_url)
        if url:
            return url
    return ""


def _normalize_images(container: Tag, base_url: str) -> None:
    """Giữ lại mỗi ảnh dưới dạng <img src=URL tuyệt đối alt=... data-caption=...>, bỏ ảnh không dùng được."""
    for pic in container.find_all("picture"):
        img = pic.find("img")
        src = pic.find("source", attrs={"srcset": True})
        if img is not None and src is not None and not _img_url(img, base_url):
            img["srcset"] = src["srcset"]
    for img in container.find_all("img"):
        url = _img_url(img, base_url)
        if not url or url.lower().split("?")[0].endswith(".svg"):
            img.decompose()
            continue
        caption = ""
        fig = img.find_parent("figure")
        if fig is not None and fig.find("figcaption") is not None:
            caption = normalize_text(fig.find("figcaption").get_text(" "))
        alt = normalize_text(img.get("alt") or img.get("title") or "")
        img.attrs = {"src": url}
        if alt:
            img.attrs["alt"] = alt
        if caption:
            img.attrs["data-caption"] = caption


def extract_meta(soup: BeautifulSoup, base_url: str = "") -> dict:
    """Lấy metadata TRƯỚC khi xoá DOM (title, mô tả, ngày đăng nằm ở <head>/<h1>)."""

    def meta(*names: str) -> str:
        for n in names:
            tag = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
            if tag and tag.get("content"):
                return normalize_text(tag["content"])
        return ""

    h1 = soup.find("h1")
    h1_text = normalize_text(h1.get_text(" ")) if h1 else ""
    og_title = meta("og:title")
    doc_title = normalize_text(soup.title.get_text()) if soup.title else ""
    canonical = ""
    link = soup.find("link", rel="canonical")
    if link and link.get("href"):
        canonical = link["href"].strip()

    published = meta("article:published_time", "og:article:published_time", "datePublished")
    if not published:
        t = soup.find("time", attrs={"datetime": True})
        if t:
            published = t["datetime"].strip()
    if not published and h1:
        # ngày dạng dd/mm/yyyy nằm ngay sau <h1> (vd "10/11/2025")
        near = " ".join(h1.find_all_next(string=True, limit=40))
        m = DATE_RE.search(near)
        if m:
            d, mo, y = map(int, m.groups())
            published = f"{y:04d}-{mo:02d}-{d:02d}"

    return {
        "title": h1_text or og_title or doc_title,
        "og_title": og_title,
        "html_title": doc_title,
        "description": meta("og:description", "description"),
        "canonical": canonical,
        "published_at": published or None,
        "og_image": _fix_url(meta("og:image", "twitter:image"), base_url),
        "site_name": meta("og:site_name"),
    }


def _heuristic_container(soup: BeautifulSoup) -> Tag | None:
    """Chọn khối có nhiều văn bản 'thật' nhất (đoạn <p>/<li> ít link), rồi đi sâu xuống con nếu con chiếm gần hết."""
    scores: dict[int, float] = defaultdict(float)
    nodes: dict[int, Tag] = {}
    for leaf in soup.find_all(["p", "li"]):
        n = _text_len(leaf)
        if n < 25 or _link_density(leaf) > 0.5:
            continue
        for parent in leaf.parents:
            if parent.name in ("div", "section", "article", "main", "td", "body"):
                scores[id(parent)] += n
                nodes[id(parent)] = parent
    if not scores:
        return soup.body
    best = max(scores, key=scores.get)
    cur = nodes[best]
    while True:
        kids = [c for c in cur.find_all(["div", "section", "article"], recursive=False) if id(c) in scores]
        if not kids:
            break
        top = max(kids, key=lambda c: scores[id(c)])
        if scores[id(top)] >= 0.85 * scores[id(cur)]:
            cur = top
        else:
            break
    return cur


def pick_container(soup: BeautifulSoup, selectors: list[str]) -> tuple[Tag | None, str]:
    for sel in selectors:
        try:
            els = soup.select(sel)
        except Exception:  # selector sai cú pháp thì bỏ qua
            log.warning("Selector không hợp lệ: %s", sel)
            continue
        if not els:
            continue
        best = max(els, key=_text_len)
        if _text_len(best) >= MIN_CONTAINER_CHARS:
            return best, f"selector:{sel}"
    return _heuristic_container(soup), "heuristic"


# ---------------------------------------------------------------- main
def clean_html(html: str, src: dict, base_url: str = "") -> tuple[str, dict]:
    """Trả về (html_sạch, thông_tin_debug_và_meta). base_url dùng để đổi URL ảnh tương đối thành tuyệt đối."""
    soup = BeautifulSoup(html, "lxml")
    meta = extract_meta(soup, base_url)
    raw_len = len(html)

    # 1) tag nhiễu + comment + phần tử ẩn
    for c in soup.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()
    for el in soup.find_all(STRIP_TAGS):
        el.decompose()
    for el in soup.find_all(True):
        if _decomposed(el) or el.attrs is None:
            continue
        style = (el.get("style") or "").replace(" ", "").lower()
        if el.has_attr("hidden") or el.get("aria-hidden") == "true" or "display:none" in style:
            el.decompose()

    # 2) selector riêng từng site
    for sel in src.get("remove_selectors", []):
        try:
            for el in soup.select(sel):
                if not _decomposed(el):
                    el.decompose()
        except Exception:
            log.warning("Selector không hợp lệ (remove): %s", sel)

    # 3) vùng nội dung
    container, method = pick_container(soup, src.get("content_selectors", []))
    if container is None:
        return "", {**meta, "method": "none", "raw_chars": raw_len, "clean_chars": 0}

    # 4) chuẩn hoá ảnh, rồi tỉa bên trong vùng nội dung
    _normalize_images(container, base_url)
    for el in reversed(container.find_all(["ul", "ol", "div", "section", "p", "li", "span"])):
        if _decomposed(el):
            continue
        if _text_len(el) <= 600 and _link_density(el) >= 0.7 and not el.find(HEADINGS):
            el.decompose()
    for a in container.find_all("a"):
        a.unwrap()
    for el in reversed(container.find_all(True)):
        if (
            not _decomposed(el)
            and el.name not in ("br", "hr", "img")
            and not el.get_text(strip=True)
            and el.find("img") is None  # khối chỉ chứa ảnh thì giữ
        ):
            el.decompose()
    for br in container.find_all("br"):
        br.replace_with("\n")

    out = (
        "<!doctype html><html><head><meta charset='utf-8'></head><body>"
        f"<div id='main-content'>{container.decode_contents()}</div></body></html>"
    )
    info = {**meta, "method": method, "raw_chars": raw_len, "clean_chars": _text_len(container)}
    return out, info


def clean_source(src: dict) -> dict:
    name = src["name"]
    out_dir = CLEAN_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = {"cleaned": 0, "empty": 0, "heuristic_fallback": 0}
    for row in iter_manifest_documents(RAW_DIR, name):
        raw_path = RAW_DIR.parent / row["path"]
        html = raw_path.read_text(encoding="utf-8")
        cleaned, info = clean_html(html, src, row["url"])
        if not cleaned or info["clean_chars"] < 200:
            log.warning("Nội dung rỗng/quá ngắn sau khi clean: %s", row["url"])
            stats["empty"] += 1
            continue
        if info["method"] == "heuristic":
            stats["heuristic_fallback"] += 1
            log.info("Dùng heuristic (selector config không khớp): %s", row["url"])
        (out_dir / f"{row['id']}.html").write_text(cleaned, encoding="utf-8")
        (out_dir / f"{row['id']}.meta.json").write_text(
            json.dumps({"url": row["url"], "fetched_at": row["fetched_at"], **info}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        stats["cleaned"] += 1
    return stats
