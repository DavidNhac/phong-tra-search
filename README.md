# Phòng trà semantic search

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
│   ├── tag.py, chunk.py       # chuẩn hoá metadata và tạo một chunk/venue
│   ├── vector_search.py       # embedding local, semantic search và đánh giá
│   ├── run.py                 # điểm vào CLI
│   ├── config.py, utils.py
├── data/
│   ├── 01_raw_html/<source>/  # *.html + manifest.jsonl (url, status, thời gian tải)
│   ├── 02_clean_html/<source>/# *.html (chỉ vùng nội dung) + *.meta.json (debug: dùng selector nào)
│   ├── 03_extracted/          # documents.jsonl, venues.jsonl, report.json   <- đầu vào bước chunking
│   │   └── images/<source>/   # ảnh đã tải (đặt tên theo sha1) + manifest.json + report.json
│   ├── 04_merged/             # venues_merged.jsonl + merge_report.json
│   ├── 05_tagged/             # venues_tagged.jsonl + tag_report.json
│   ├── 04_chunks/             # chunks.jsonl (một venue là một đơn vị ban đầu)
│   └── 06_embeddings/         # venue_embeddings.jsonl
└── tests/                     # test offline bằng HTML mẫu
```

## Chạy

```bash
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m src.pipeline.run all                 # crawl tới chunk; chưa tạo embedding
python -m src.pipeline.run crawl --source vinpearl
python -m src.pipeline.run clean               # chạy lại clean sau khi chỉnh selector (không cần crawl lại)
python -m src.pipeline.run extract
python -m src.pipeline.run images              # tải ảnh (chạy sau extract; thêm --force để tải lại)
python -m src.pipeline.run merge               # chuẩn hoá tên + gộp quán trùng
python -m src.pipeline.run tag
python -m src.pipeline.run chunk
python -m src.pipeline.run embed               # tải model multilingual-e5-small lần đầu nếu chưa có cache
python -m src.pipeline.run search --query "phòng trà có nhạc sống buổi tối"
python -m src.pipeline.run evaluate-search
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
- Source `trixie_official` lấy nội dung trang chính thức của Trixie. Nếu trang single-venue không đưa địa chỉ
  vào phần nội dung đã làm sạch, `venue_addresses` trong `sources.yaml` cung cấp địa chỉ đã xác minh và giữ
  provenance theo source đó.

## Ảnh
- `clean` giữ lại thẻ `<img>` trong vùng nội dung (xử lý lazy-load `data-src`, `srcset`, URL tương đối), bỏ logo/icon/svg.
- `extract` gắn mỗi ảnh vào **section đang mở** (ảnh nằm dưới tiêu đề quán nào thì thuộc quán đó):
  `venues.jsonl -> images[]`, `documents.jsonl -> sections[].images[]` và `cover_image` (og:image).
- `images` tải file venue và cover về `data/03_extracted/images/<source>/`, lọc ảnh nhỏ/logo/banner, rồi ghi lại vào
  jsonl các trường `status`, `local_path`, `width`, `height`. Ảnh section của venue dùng cùng trạng thái với venue;
  ảnh section không thuộc venue không được tải thêm trong bài listing. Xem `images/report.json` để biết quán nào chưa có ảnh.
- Muốn chỉnh ngưỡng lọc: thêm khối `images:` vào `defaults` hoặc từng source trong `sources.yaml`
  (các khoá: `enabled`, `max_per_venue`, `min_width`, `min_height`, `min_bytes`, `skip_url_patterns`...). Không bắt buộc.
- Chạy lại `extract` sẽ ghi đè 2 file jsonl nên cần chạy lại `images` (nhanh vì đã có cache trong `manifest.json`).

## Gộp quán trùng (merge)
- Đọc `03_extracted/venues.jsonl`, ghi `04_merged/venues_merged.jsonl`: mỗi quán thật = 1 dòng, gồm tên chuẩn,
  `aliases`, `addresses` (đã bỏ bản trùng), giờ/giá, `images` (bỏ ảnh trùng), và `source_records` (bản ghi gốc từng nguồn).
- `source_addresses` giữ lại từng địa chỉ kèm nguồn và `venue_id`; nếu cấu hình ghi đè `addresses`, danh sách
  `addresses` là giá trị đã duyệt còn `source_addresses` vẫn giữ nguyên dữ liệu đầu vào.
- Chỉ gộp khi tên chuẩn trùng VÀ địa chỉ không mâu thuẫn. Ca nghi ngờ KHÔNG tự gộp mà vào `merge_report.json`:
  `address_conflicts`, `possible_duplicates` (tên gần giống), `same_address_different_name`, `hours_to_review`, `field_conflicts`.
- Chỉnh bằng `config/venue_aliases.yaml` (ép tên chuẩn, ép gộp, hoặc `overrides` cho `addresses`, `opening_hours`,
  `price_text`; đặt `null` để bỏ giá trị chưa xác minh) rồi chạy lại `merge` - nhanh, không cần crawl lại.
- Với chunk/embedding, dùng `source_records[].embedding_text` thay cho `text` và lấy metadata từ các trường gộp ở cấp venue.
  Khi một trường bị ghi đè, dòng dữ liệu nguồn tương ứng bị bỏ khỏi `embedding_text`, còn `text` gốc được giữ để truy vết.

## Tag, chunk và tìm kiếm
- `tag` chuẩn hoá quận, giờ mở cửa qua đêm/cả ngày, giá VND và giữ lại các quan sát giá kèm nguồn.
- `chunk` tạo một chunk cho mỗi venue, giữ venue làm đơn vị ngữ nghĩa thay vì chia nhỏ theo độ dài.
- `embed` dùng `intfloat/multilingual-e5-small` trên máy local (không gửi nội dung lên API). E5 dùng prefix
  `passage:` cho venue và `query:` cho truy vấn. Lần chạy đầu cần tải model về cache Hugging Face.
- `evaluate-search` dùng `config/search_eval.yaml` để báo Recall@k và MRR; kết quả phụ thuộc độ bao phủ của
  nhãn liên quan trong bộ query nhỏ này, nên cần bổ sung query trước khi coi là đánh giá đại diện.
