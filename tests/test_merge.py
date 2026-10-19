"""Test bước merge. Dữ liệu mô phỏng theo kết quả thật của dự án (tên, giờ mở cửa, số ảnh); địa chỉ bên Lữ Hành là giả định."""
import pytest

from src.pipeline.merge import clean_hours, clean_name, merge_venues, suspicious_hours

ORDER = ["vinpearl", "luhanhvietnam"]
ALIASES = {"Phòng trà ca nhạc tại Hà Nội Like Coffee 32 Hào Nam": "Like Coffee", "Phòng trà Hà Nội Phố": "Hà Nội Phố"}


def rec(source, rank, name, addrs, hours=None, price=None, n_img=1, tagline=""):
    tag = f"{source}-{rank}"
    return {
        "venue_id": f"{tag}", "source": source, "url": f"https://{source}.test/post", "rank_in_article": rank,
        "name": name, "tagline": tagline, "addresses": addrs, "opening_hours": hours, "price_text": price,
        "description": f"Mô tả {name}", "text": f"{name}. Mô tả", "published_at": "2025-11-10", "fetched_at": "t",
        "images": [
            {"src": f"https://{source}.test/{tag}-{k}.jpg", "alt": "", "caption": "", "status": "ok",
             "local_path": f"03_extracted/images/{source}/{tag}-{k}.jpg", "width": 700, "height": 466, "sha1": f"{tag}-{k}"}
            for k in range(n_img)
        ],
    }


VIN = [
    (1, "Nhà sàn Art Cafe", ["ngõ 291 Ngô Xuân Quảng, xã Gia Lâm, Hà Nội"]),
    (2, "Phòng trà Trịnh Ca Hà Nội", ["ngõ 233 Tô Hiệu, Nghĩa Đô, Hà Nội (nằm tại ngách thứ 2 bên trái)"]),
    (3, "Trixie Cafe & Lounge", ["165 Thái Hà, phường Đống Đa, Hà Nội"]),
    (4, "Phòng trà Hà Nội Acoustic Cafe", ["236 Hàng Bông, phường Cửa Nam, Hà Nội"]),
    (5, "Xóm cafe", ["Tầng 1 N6E Trung Hoà, phường Yên Hòa, Hà Nội", "Số A2 Lô BT1A Bùi Xuân Phái, phường Từ Liêm, Hà Nội"]),
    (6, "Phòng trà Hà Nội nổi tiếng Ấy Lounge", ["63 Hàng Bún, phường Ba Đình, Hà Nội"]),
    (7, "Sweet Home Lounge", ["19 Hàng Khay, phường Cửa Nam, Hà Nội"]),
    (8, "G4U Cafe Ca Nhạc", ["29 Nguyễn Khang, phường Cầu Giấy, Hà Nội"]),
    (9, "Phòng trà ca nhạc tại Hà Nội Like Coffee 32 Hào Nam", ["32 Hào Nam, phường Ô Chợ Dừa, Hà Nội"]),
    (10, "Hay Bar", ["12 Tạ Hiện, phường Hoàn Kiếm, Hà Nội"]),
    (11, "Polygon Musik", ["số 29 ngách 31 ngõ 167 Tây Sơn, Hà Nội"]),
    (12, "Phòng trà Hà Nội Phố", ["Số 38 ngõ 106 Hoàng Quốc Việt, phường Nghĩa Đô, Hà Nội"]),
    (13, "Phòng trà Hà Nội Giang Coffee trên đường Trần Phú", ["119B Trần Phú, phường Hà Đông, Hà Nội"]),
]
LH = [
    (1, "Trixie Cafe & Lounge", ["165 Thái Hà, Đống Đa, Hà Nội"], "6h00 - 23h30.", None),
    (2, "Phòng trà Trịnh Ca", ["Ngõ 233 Tô Hiệu, Nghĩa Đô, Hà Nội"], "7h00 - 23h00.", "20.000đ - 55.000đ."),
    (3, "Ấy Lounge", ["63 Hàng Bún, Ba Đình, Hà Nội"], "7h00 - 23h00.", "50.000đ."),
    (4, "Acoustic Café", ["236 Hàng Bông, Hoàn Kiếm, Hà Nội"], None, "100.000đ – 440.000đ."),
    (5, "Sweet Home Lounge", ["19 Hàng Khay, Hoàn Kiếm, Hà Nội"], "10h00 – 2h00.", None),
    (6, "Phòng trà ‘Hà Nội Phố’", ["38 ngõ 106 Hoàng Quốc Việt, Cầu Giấy, Hà Nội"], "cả ngày.", "25.000đ - 100.000đ."),
    (7, "Phòng trà Hoa Tâm Các", ["Ngõ 1 Hồ Tây, Tây Hồ, Hà Nội"], "7h00 - 23h00.", "50.000đ - 100.000đ."),
    (8, "Polygon Musik", ["29 ngách 31 ngõ 167 Tây Sơn, Đống Đa, Hà Nội"], None, "40.000đ – 100.000đ."),
    (9, "Hay Bar", ["12 Tạ Hiện, Hoàn Kiếm, Hà Nội"], "7h00 – 11h30.", "100.000đ."),
    (10, "Zone Music", ["Ngõ 5 Tạ Quang Bửu, Hai Bà Trưng, Hà Nội"], "cả ngày.", None),
]


def all_records():
    recs = [rec("vinpearl", r, n, a, n_img=0 if r in (9, 13) else 1) for r, n, a in VIN]
    recs += [rec("luhanhvietnam", r, n, a, h, p, n_img=3 if r == 10 else 2) for r, n, a, h, p in LH]
    return recs


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Phòng trà Hà Nội nổi tiếng Ấy Lounge", "Ấy Lounge"),
        ("Phòng trà Trịnh Ca Hà Nội", "Trịnh Ca"),
        ("Phòng trà Hà Nội Acoustic Cafe", "Acoustic Cafe"),
        ("Phòng trà Hà Nội Giang Coffee trên đường Trần Phú", "Giang Coffee"),
        ("Phòng trà ‘Hà Nội Phố’", "Hà Nội Phố"),   # không được cắt "Hà Nội" thành "Phố"
        ("Phòng trà Hà Nội Phố", "Hà Nội Phố"),
        ("Trixie Cafe & Lounge", "Trixie Cafe & Lounge"),
        ("Xóm cafe", "Xóm cafe"),
    ],
)
def test_clean_name(raw, expected):
    assert clean_name(raw) == expected


def test_alias_overrides():
    from src.pipeline.merge import fold

    al = {fold(k): v for k, v in ALIASES.items()}
    assert clean_name("Phòng trà ca nhạc tại Hà Nội Like Coffee 32 Hào Nam", al) == "Like Coffee"


def test_hours():
    assert clean_hours("6h00 - 23h30.") == "6h00 - 23h30"
    assert clean_hours("10h00 – 2h00.") == "10h00 - 2h00"
    assert suspicious_hours("7h00 - 11h30")          # đóng cửa trước trưa
    assert not suspicious_hours("10h00 - 2h00")      # qua đêm là bình thường
    assert not suspicious_hours("cả ngày")


def test_merge_real_shape():
    merged, rep = merge_venues(all_records(), ORDER, ALIASES)
    assert rep["records_in"] == 23
    assert len(merged) == 15                          # 13 + 10 - 8 quán trùng
    by = {v["canonical_name"]: v for v in merged}
    both = {n for n, v in by.items() if v["n_sources"] == 2}
    assert both == {"Trixie Cafe & Lounge", "Trịnh Ca", "Ấy Lounge", "Acoustic Café", "Sweet Home Lounge",
                    "Hà Nội Phố", "Polygon Musik", "Hay Bar"}
    assert "Hà Nội Phố" in by and "Like Coffee" in by and "Giang Coffee" in by
    assert rep["address_conflicts"] == []

    t = by["Trixie Cafe & Lounge"]
    assert len(t["addresses"]) == 1                   # 2 cách ghi cùng 1 địa chỉ -> giữ 1
    assert t["opening_hours"] == "6h00 - 23h30"       # giờ lấy từ nguồn có, đã bỏ dấu chấm
    assert len(t["images"]) == 2 + 1                  # ảnh của cả 2 nguồn
    assert [s["source"] for s in t["source_records"]] == ["vinpearl", "luhanhvietnam"]   # giữ nguồn gốc
    assert len(by["Acoustic Café"]["addresses"]) == 1   # cùng "236 Hàng Bông", chỉ khác cách ghi phường
    assert len(by["Xóm cafe"]["addresses"]) == 2        # nhiều chi nhánh thật thì giữ đủ
    assert rep["same_address_different_name"] == []     # Hàng Bông / Hàng Khay là 2 địa chỉ khác nhau
    # tên chuẩn lấy bản không cần sửa (có dấu), tên gốc dài bên kia nguồn được giữ làm alias
    assert by["Acoustic Café"]["aliases"] == ["Phòng trà Hà Nội Acoustic Cafe"]


def test_review_lists():
    _, rep = merge_venues(all_records(), ORDER, ALIASES)
    assert [h["venue"] for h in rep["hours_to_review"]] == ["Hay Bar"]
    assert set(rep["single_source"]) == {
        "Nhà sàn Art Cafe", "Xóm cafe", "G4U Cafe Ca Nhạc", "Like Coffee", "Giang Coffee", "Hoa Tâm Các", "Zone Music"}


def test_same_name_different_address_not_merged_unless_forced():
    a = rec("vinpearl", 1, "Hay Bar", ["12 Tạ Hiện, Hoàn Kiếm, Hà Nội"])
    b = rec("luhanhvietnam", 1, "Hay Bar", ["99 Lê Lợi, Quận 1, TP HCM"])
    merged, rep = merge_venues([a, b], ORDER)
    assert len(merged) == 2 and len(rep["address_conflicts"]) == 1
    assert {v["venue_key"] for v in merged} == {"hay-bar", "hay-bar-2"}
    merged2, rep2 = merge_venues([a, b], ORDER, force_merge=["Hay Bar"])
    assert len(merged2) == 1 and rep2["address_conflicts"] == []


def test_missing_address_still_merges():
    a = rec("vinpearl", 1, "Hay Bar", ["12 Tạ Hiện, Hoàn Kiếm, Hà Nội"])
    b = rec("luhanhvietnam", 1, "Hay Bar", [])
    assert len(merge_venues([a, b], ORDER)[0]) == 1


def test_same_source_never_merged():
    a = rec("vinpearl", 1, "Hay Bar", ["12 Tạ Hiện, Hà Nội"])
    b = rec("vinpearl", 2, "Hay Bar", ["12 Tạ Hiện, Hà Nội"])
    assert len(merge_venues([a, b], ORDER)[0]) == 2


def test_typo_names_flagged_as_possible_duplicates():
    a = rec("vinpearl", 1, "Phòng trà Hoa Tâm Cúc", ["Ngõ 1 Hồ Tây, Tây Hồ, Hà Nội"])
    b = rec("luhanhvietnam", 1, "Phòng trà Hoa Tâm Các", ["Ngõ 1 Hồ Tây, Tây Hồ, Hà Nội"])
    merged, rep = merge_venues([a, b], ORDER)
    assert len(merged) == 2                              # không tự gộp khi chỉ giống nhau 1 phần
    assert len(rep["possible_duplicates"]) == 1          # nhưng được báo để người xem


def test_images_deduplicated_by_sha1():
    a = rec("vinpearl", 1, "Hay Bar", ["12 Tạ Hiện"], n_img=1)
    b = rec("luhanhvietnam", 1, "Hay Bar", ["12 Tạ Hiện"], n_img=1)
    b["images"][0]["sha1"] = a["images"][0]["sha1"]      # cùng 1 ảnh ở 2 nguồn
    assert len(merge_venues([a, b], ORDER)[0][0]["images"]) == 1


def test_address_matching_ignores_ward_names_but_not_house_numbers():
    from src.pipeline.merge import addr_similar

    assert addr_similar("236 Hàng Bông, phường Cửa Nam, Hà Nội", "Số 236 Hàng Bông, Hoàn Kiếm, Hà Nội")
    assert addr_similar("Tầng M, Tòa nhà Sông Hồng, 165 Thái Hà, Hà Nội", "165 Thái Hà, phường Đống Đa")
    assert not addr_similar("10 Hàng Bông, Hà Nội", "236 Hàng Bông, Hà Nội")   # cùng phố, khác số nhà
    assert not addr_similar("19 Hàng Khay, Hà Nội", "236 Hàng Bông, Hà Nội")
