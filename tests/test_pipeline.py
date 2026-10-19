"""Test offline (không cần mạng) cho clean + extract, dùng fixture mô phỏng cấu trúc bài Vinpearl."""
from pathlib import Path

from src.pipeline.clean import clean_html
from src.pipeline.config import get_sources, load_config
from src.pipeline.extract import extract_document

FIXTURE = Path(__file__).parent / "fixtures" / "vinpearl_sample.html"


def _run():
    src = get_sources(load_config(), "vinpearl")[0]
    url = "https://vinpearl.com/vi/phong-tra-ha-noi"
    cleaned, info = clean_html(FIXTURE.read_text(encoding="utf-8"), src, url)
    meta = {"url": url, "fetched_at": "2026-10-18T00:00:00+00:00", **info}
    doc, venues = extract_document(cleaned, meta, src, "testdoc", "vinpearl")
    return cleaned, info, doc, venues


def test_noise_removed():
    cleaned, _, doc, _ = _run()
    for noise in ["Khách sạn", "Ưu đãi khuyến mãi", "Tìm kiếm", "Hotline", "var x", "color:red", "Bài viết liên quan"]:
        assert noise not in cleaned, noise
    assert ">>>" not in doc["text"]
    assert "Mục lục" not in doc["text"]


def test_promo_tail_cut():
    _, _, doc, _ = _run()
    assert "VinWonders" not in doc["text"]
    assert "mang tính chất tổng hợp" not in doc["text"]


def test_meta():
    _, info, doc, _ = _run()
    assert doc["title"].startswith("Chill cùng âm nhạc")
    assert doc["published_at"] == "2025-11-10"
    assert "<a " not in doc["text"]


def test_venues():
    _, _, _, venues = _run()
    assert [v["rank_in_article"] for v in venues] == [1, 2, 5]
    v1 = venues[0]
    assert v1["name"] == "Nhà sàn Art Cafe"
    assert v1["tagline"].startswith("phòng trà ở Hà Nội")
    assert v1["addresses"] == ["ngõ 291 Ngô Xuân Quảng, xã Gia Lâm, Hà Nội"]
    assert len(venues[2]["addresses"]) == 2  # nhiều chi nhánh
    assert "Địa chỉ" not in venues[0]["description"]


def test_luhanhvietnam_labels_and_cut():
    src = get_sources(load_config(), "luhanhvietnam")[0]
    html = """<html><head><title>x</title><meta property="og:title" content="Top phòng trà"></head><body>
    <div class="post-content"><h1>Top những phòng trà ở Hà Nội</h1>
    <h2>Các phòng trà ở Hà Nội được yêu thích hàng đầu</h2>
    <p>Hà Nội có rất nhiều phòng trà để thưởng nhạc và thư giãn vào buổi tối, phù hợp với nhiều đối tượng khách khác nhau từ giới trẻ đến người lớn tuổi.</p>
    <h3>1. Trixie Cafe &amp; Lounge</h3>
    <ul><li>Địa chỉ: 165 Thái Hà, Đống Đa, Hà Nội</li><li>Giờ mở cửa: 18h - 23h</li><li>Giá từ: 150.000 - 300.000 VNĐ</li></ul>
    <p>Trixie được đầu tư kỹ lưỡng về sân khấu, âm thanh và ánh sáng, thường tổ chức mini show của nhiều nghệ sĩ nổi tiếng.</p>
    <p>Hà Lê (tổng hợp) - luhanhvietnam.com.vn</p>
    <p>Tin xem nhiều: nhiễu phía sau byline không được lọt vào dữ liệu</p></div></body></html>"""
    cleaned, info = clean_html(html, src)
    meta = {"url": "u", "fetched_at": "t", **info}
    doc, venues = extract_document(cleaned, meta, src, "d2", "luhanhvietnam")
    assert "nhiễu phía sau" not in doc["text"]
    assert len(venues) == 1
    v = venues[0]
    assert v["name"] == "Trixie Cafe & Lounge"
    assert v["opening_hours"] == "18h - 23h"
    assert v["price_text"].startswith("150.000")
    assert v["addresses"] == ["165 Thái Hà, Đống Đa, Hà Nội"]


# ------------------------------------------------------------------ ảnh
def test_images_assigned_to_right_venue():
    _, _, doc, venues = _run()
    v1, v2, v5 = venues
    # lazy-load: lấy data-src, đổi thành URL tuyệt đối, noscript không tạo bản trùng
    assert [i["src"] for i in v1["images"]] == ["https://vinpearl.com/img/phong-tra-02.jpg"]
    assert v1["images"][0]["alt"] == "Phòng trà Hà Nội"
    # srcset: chọn bản rộng nhất; svg bị bỏ; figcaption thành caption
    assert [i["src"] for i in v2["images"]] == ["https://vinpearl.com/img/trinh-ca-800.jpg"]
    assert v2["images"][0]["caption"] == "Không gian Trịnh Ca"
    assert v5["images"] == []


def test_noise_images_not_collected():
    cleaned, _, doc, venues = _run()
    all_src = [i["src"] for s in doc["sections"] for i in s["images"]]
    assert not any("logo" in u for u in all_src)          # logo trong header
    assert not any("vinwonders-13" in u for u in all_src)  # ảnh quảng cáo sau điểm cắt
    assert "https://vinpearl.com/vi/a.jpg" in all_src      # ảnh intro (URL tương đối -> tuyệt đối)
    assert doc["cover_image"]["src"] == "https://statics.vinpearl.com/phong-tra-ha-noi-thumb.jpg"


def test_og_image_double_url_fixed():
    from src.pipeline.clean import _fix_url

    bad = "/tour-du-lichhttps://luhanhvietnam.com.vn/vnt_upload/a.jpg"
    assert _fix_url(bad, "https://luhanhvietnam.com.vn/") == "https://luhanhvietnam.com.vn/vnt_upload/a.jpg"
    assert _fix_url("data:image/gif;base64,AAA") == ""


def test_download_and_filter(tmp_path):
    import io

    import pytest

    PIL = pytest.importorskip("PIL.Image")
    from src.pipeline.images import enrich

    def jpeg(w, h):
        b = io.BytesIO()
        PIL.new("RGB", (w, h), (200, 60, 60)).save(b, "JPEG")
        return b.getvalue()

    class FakeFetcher:
        blobs = {
            "https://x.test/big.jpg": jpeg(800, 600),
            "https://x.test/tiny.jpg": jpeg(60, 60),
            "https://x.test/big2.jpg": jpeg(900, 700),
            "https://x.test/big3.jpg": jpeg(1000, 700),
        }

        def __init__(self):
            self.calls = []

        def get_binary(self, url, referer=None, max_bytes=0):
            self.calls.append((url, referer))
            if url in self.blobs:
                return 200, self.blobs[url], "image/jpeg", None
            return 404, None, None, "HTTP 404"

    def item(u):
        return {"src": u, "alt": "", "caption": ""}

    venue = {
        "venue_id": "d#01", "source": "s", "url": "https://x.test/post",
        "images": [item(u) for u in [
            "https://x.test/big.jpg", "https://x.test/tiny.jpg", "https://x.test/missing.jpg",
            "https://x.test/logo.jpg", "https://x.test/big2.jpg", "https://x.test/big3.jpg",
        ]],
    }
    cfg = {"images": {"min_bytes": 0, "max_per_venue": 2}}
    cache, fetcher = {}, FakeFetcher()
    data_root = tmp_path / "data"
    stats = enrich([venue], [], cfg, {"s": {}}, fetcher, data_root / "images", data_root, cache, {"s"})

    st = [i["status"] for i in venue["images"]]
    assert st == ["ok", "skipped_small", "failed", "skipped_pattern", "ok", "skipped_limit"]
    assert venue["images"][0]["status"] == "ok"
    assert (data_root / venue["images"][0]["local_path"]).exists()
    assert venue["images"][0]["width"] == 800
    assert venue["images"][3]["status"] == "skipped_pattern"
    assert all(ref == "https://x.test/post" for _, ref in fetcher.calls)   # gửi Referer
    assert not any("logo" in u for u, _ in fetcher.calls)                  # ảnh bị lọc không tải
    assert stats["ok"] == 2 and stats["skipped_limit"] == 1
    assert not any("big3" in u for u, _ in fetcher.calls)  # vượt giới hạn thì không tải
