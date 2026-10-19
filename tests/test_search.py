from src.pipeline.chunk import build_chunks
from src.pipeline.tag import tag_venue
from src.pipeline.vector_search import build_embeddings, cosine_ranking, evaluate_queries


def _venue(**overrides):
    venue = {
        "venue_key": "test-venue",
        "canonical_name": "Test Venue",
        "aliases": [],
        "addresses": ["12 Tạ Hiện, Q. Hoàn Kiếm, Hà Nội"],
        "source_addresses": [
            {"address": "12 Tạ Hiện, Q. Hoàn Kiếm, Hà Nội", "source": "source-a", "venue_id": "a#1"}
        ],
        "opening_hours": "10h00 - 2h00",
        "price_text": "40.000đ – 100.000đ",
        "taglines": ["nhạc sống"],
        "n_sources": 1,
        "source_records": [
            {
                "source": "source-a",
                "url": "https://example.test/venue",
                "opening_hours": "10h00 - 2h00",
                "price_text": "40.000đ – 100.000đ",
                "description": "Giá đồ uống thường dao động từ 100.000 - 200.000 VND/món.\nKhông gian nhạc sống.",
                "embedding_text": "Test Venue.\nGiá: 40.000đ – 100.000đ\nKhông gian nhạc sống.",
            }
        ],
    }
    venue.update(overrides)
    return venue


def test_structured_metadata_parses_overnight_hours_price_and_district():
    tagged = tag_venue(_venue())
    assert tagged["district"] == "Hoàn Kiếm"
    assert tagged["district_candidates"] == ["Hoàn Kiếm"]
    assert tagged["open_time"] == "10:00"
    assert tagged["close_time"] == "02:00"
    assert tagged["overnight"] is True
    assert tagged["all_day"] is False
    assert tagged["price_min_vnd"] == 40_000
    assert tagged["price_max_vnd"] == 100_000
    assert tagged["price_currency"] == "VND"
    assert tagged["price_observations"] == [
        {
            "source": "source-a",
            "text": "Giá đồ uống thường dao động từ 100.000 - 200.000 VND/món",
            "amounts_vnd": [100_000, 200_000],
        }
    ]


def test_all_day_and_suppressed_hours_are_not_guessed():
    all_day = tag_venue(_venue(opening_hours="cả ngày"))
    assert all_day["all_day"] is True
    assert all_day["open_time"] is None and all_day["close_time"] is None

    suppressed = tag_venue(_venue(opening_hours=None))
    assert suppressed["opening_hours_status"] == "not_provided"
    assert suppressed["open_time"] is None and suppressed["close_time"] is None


def test_conflicting_districts_are_not_collapsed_into_one():
    venue = _venue(source_addresses=[
        {"address": "12 Tạ Hiện, Q. Hoàn Kiếm, Hà Nội", "source": "a", "venue_id": "a#1"},
        {"address": "99 Lê Lợi, Q. Ba Đình, Hà Nội", "source": "b", "venue_id": "b#1"},
    ])
    tagged = tag_venue(venue)
    assert tagged["district"] is None
    assert tagged["district_candidates"] == ["Ba Đình", "Hoàn Kiếm"]


def test_one_venue_produces_one_chunk_without_unverified_field_lines():
    tagged = tag_venue(_venue(opening_hours=None, price_text=None))
    chunk = build_chunks([tagged])[0]
    assert chunk["chunk_id"] == "test-venue"
    assert "Test Venue" in chunk["text"]
    assert "Không gian nhạc sống." in chunk["text"]
    assert "40.000đ" not in chunk["text"]
    assert chunk["metadata"]["overnight"] is False
    assert len(build_chunks([tagged])) == 1


class FakeEncoder:
    def encode(self, texts, **kwargs):
        vectors = []
        for text in texts:
            vectors.append([1.0, 0.0] if "query:" in text else [1.0, 0.0])
        return np.asarray(vectors, dtype=float)


def test_embedding_generation_and_evaluation_metrics():
    chunks = [
        {"chunk_id": "a", "venue_key": "a", "text": "venue A"},
        {"chunk_id": "b", "venue_key": "b", "text": "venue B"},
    ]
    encoder = FakeEncoder()
    embeddings = build_embeddings(chunks, encoder, "fake")
    ranking = cosine_ranking([1.0, 0.0], chunks, embeddings, top_k=1)
    assert ranking[0]["chunk"]["venue_key"] == "a"

    report = evaluate_queries(
        [{"query": "query", "relevant_venue_keys": ["a"]}],
        chunks,
        embeddings,
        encoder,
        top_k=1,
    )
    assert report["recall_at_1"] == 1.0
    assert report["mean_reciprocal_rank"] == 1.0
import numpy as np
