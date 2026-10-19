# Phòng trà semantic search - Bước 1: Crawl -> Clean -> Extract

## Cấu trúc thư mục (mỗi thư mục data/ = 1 giai đoạn, đầu ra của giai đoạn trước là đầu vào của giai đoạn sau)

```
phong-tra-search/
├── config/sources.yaml        # TOÀN BỘ cấu hình theo site: seeds, selector, regex lọc nhiễu
├── src/pipeline/
│   ├── crawl.py               # 1a. tải HTML gốc  -> data/01_raw_html
│   ├── clean.py               # 1b. bỏ nhiễu DOM  -> data/02_clean_html
│   ├── extract.py             # 1c. trích xuất    -> data/03_extracted
│   ├── images.py              # 1d. tải ảnh       -> data/03_extracted/images
│   ├── merge.py               # 2.  gộp quán trùng -> data/04_merged
│   ├── run.py                 # điểm vào CLI
│   ├── config.py, utils.py
├── data/
│   ├── 01_raw_html/<source>/  # *.html + manifest.jsonl (url, status, thời gian tải)
│   ├── 02_clean_html/<source>/# *.html (chỉ vùng nội dung) + *.meta.json (debug: dùng selector nào)
│   ├── 03_extracted/          # documents.jsonl, venues.jsonl, report.json   <- đầu vào bước chunking
│   │   └── images/<source>/   # ảnh đã tải (đặt tên theo sha1) + manifest.json + report.json
│   ├── 04_merged/             # venues_merged.jsonl (mỗi quán 1 bản ghi) + merge_report.json
│   └── (các thư mục chunk/tag/embedding rỗng: sẽ đặt lại số thứ tự khi tới bước đó)
└── tests/                     # test offline bằng HTML mẫu
```

## Chạy

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m src.pipeline.run all                 # chạy cả 3 giai đoạn
python -m src.pipeline.run crawl --source vinpearl
python -m src.pipeline.run clean               # chạy lại clean sau khi chỉnh selector (không cần crawl lại)
python -m src.pipeline.run extract
python -m src.pipeline.run merge              # chuẩn hoá tên + gộp quán trùng (chạy sau images)
python -m src.pipeline.run images             # tải ảnh (chạy sau extract; thêm --force để tải lại)
pytest                                         # test offline
```

## Quy trình tinh chỉnh (quan trọng)
1. Chạy `crawl`, mở 1 file trong `01_raw_html/` bằng trình duyệt, F12 tìm class của vùng nội dung bài viết.
2. Sửa `content_selectors` / `remove_selectors` trong `sources.yaml`.
3. Chạy lại `clean`, xem `02_clean_html/<source>/*.meta.json` (trường `method`: `selector:...` hay `heuristic`).
4. Chạy `extract`, đọc `report.json` và vài dòng `venues.jsonl` để kiểm tra bằng mắt.
5. Với luhanhvietnam: xem `01_raw_html/luhanhvietnam/discovered_links.json` để biết link nào được/không được chọn.

## Đầu ra bước này
- `documents.jsonl`: mỗi trang 1 dòng (title, description, published_at, sections[], text, content_hash).
- `venues.jsonl`: mỗi phòng trà 1 dòng (name, tagline, addresses[], description, text) -> chunk tự nhiên cho embedding.

## Ảnh
- `clean` giữ lại thẻ `<img>` trong vùng nội dung (xử lý lazy-load `data-src`, `srcset`, URL tương đối), bỏ logo/icon/svg.
- `extract` gắn mỗi ảnh vào **section đang mở** (ảnh nằm dưới tiêu đề quán nào thì thuộc quán đó):
  `venues.jsonl -> images[]`, `documents.jsonl -> sections[].images[]` và `cover_image` (og:image).
- `images` tải file về `data/03_extracted/images/<source>/`, lọc ảnh nhỏ/logo/banner, rồi ghi lại vào 2 file jsonl
  các trường `status`, `local_path`, `width`, `height`. Xem `images/report.json` để biết quán nào chưa có ảnh.
- Muốn chỉnh ngưỡng lọc: thêm khối `images:` vào `defaults` hoặc từng source trong `sources.yaml`
  (các khoá: `enabled`, `max_per_venue`, `min_width`, `min_height`, `min_bytes`, `skip_url_patterns`...). Không bắt buộc.
- Chạy lại `extract` sẽ ghi đè 2 file jsonl nên cần chạy lại `images` (nhanh vì đã có cache trong `manifest.json`).

## Gộp quán trùng (merge)
- Đọc `03_extracted/venues.jsonl`, ghi `04_merged/venues_merged.jsonl`: mỗi quán thật = 1 dòng, gồm tên chuẩn,
  `aliases`, `addresses` (đã bỏ bản trùng), giờ/giá, `images` (bỏ ảnh trùng), và `source_records` (bản ghi gốc từng nguồn).
- Chỉ gộp khi tên chuẩn trùng VÀ địa chỉ không mâu thuẫn. Ca nghi ngờ KHÔNG tự gộp mà vào `merge_report.json`:
  `address_conflicts`, `possible_duplicates` (tên gần giống), `same_address_different_name`, `hours_to_review`, `field_conflicts`.
- Chỉnh bằng `config/venue_aliases.yaml` (ép tên chuẩn, ép gộp) rồi chạy lại `merge` - nhanh, không cần crawl lại.
