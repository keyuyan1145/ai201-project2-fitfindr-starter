"""
Tests for search_listings() in tools.py.

Run from the project root:
    pytest tests/test_tools.py -v
"""

import pytest
from tools import search_listings


# ── helpers ───────────────────────────────────────────────────────────────────

def ids(results):
    return [r["id"] for r in results]


# ── valid scenario ─────────────────────────────────────────────────────────────

def test_basic_keyword_match_returns_results():
    results = search_listings("graphic tee")
    assert len(results) > 0

def test_basic_keyword_match_returns_listing_fields():
    results = search_listings("graphic tee")
    for r in results:
        assert "id" in r
        assert "title" in r
        assert "price" in r
        assert "size" in r
        assert "platform" in r

def test_best_match_ranked_first():
    # lst_006 has "graphic tee" in title, description, and style_tags — highest overlap
    results = search_listings("graphic tee")
    assert results[0]["id"] == "lst_006"

def test_results_sorted_by_descending_score():
    # Compute scores independently and confirm the returned order matches
    results = search_listings("vintage graphic tee")
    assert len(results) >= 2

    def score(listing, keywords):
        searchable = " ".join([
            listing.get("title", ""),
            listing.get("description", ""),
            listing.get("category", ""),
            listing.get("brand", "") or "",
            " ".join(listing.get("style_tags", [])),
            " ".join(listing.get("colors", [])),
        ]).lower()
        return sum(searchable.count(kw) for kw in keywords)

    keywords = ["vintage", "graphic", "tee"]
    scores = [score(r, keywords) for r in results]
    assert scores == sorted(scores, reverse=True)

def test_single_keyword_match():
    results = search_listings("denim")
    assert len(results) > 0
    # Every result should contain "denim" somewhere in its searchable fields
    for r in results:
        searchable = " ".join([
            r.get("title", ""),
            r.get("description", ""),
            r.get("category", ""),
            r.get("brand", "") or "",
            " ".join(r.get("style_tags", [])),
            " ".join(r.get("colors", [])),
        ]).lower()
        assert "denim" in searchable


# ── price filter ───────────────────────────────────────────────────────────────

def test_price_filter_excludes_above_max():
    results = search_listings("vintage", max_price=25.0)
    for r in results:
        assert r["price"] <= 25.0

def test_price_filter_includes_exact_max():
    # lst_006 is exactly $24.00 — included at max_price=24.0
    results = search_listings("graphic tee", max_price=24.0)
    assert any(r["id"] == "lst_006" for r in results)

def test_price_filter_at_boundary():
    # lst_001 Levi's is exactly $38.00 — included at max_price=38.0, excluded at 37.99
    results_at = search_listings("vintage denim", max_price=38.0)
    results_below = search_listings("vintage denim", max_price=37.99)
    assert any(r["id"] == "lst_001" for r in results_at)
    assert not any(r["id"] == "lst_001" for r in results_below)

def test_price_filter_with_keyword_match():
    # "graphic tee" under $25: lst_002 ($18) and lst_006 ($24) qualify; lst_004 ($45) does not
    results = search_listings("graphic tee", max_price=25.0)
    result_ids = ids(results)
    assert "lst_006" in result_ids
    assert "lst_002" in result_ids
    for r in results:
        assert r["price"] <= 25.0


# ── size filter ────────────────────────────────────────────────────────────────

def test_size_filter_exact_match():
    # lst_004 track jacket is size "M"
    results = search_listings("jacket", size="M")
    assert any(r["id"] == "lst_004" for r in results)

def test_size_filter_substring_match():
    # lst_002 is size "S/M" — should match size query "M"
    results = search_listings("tee", size="M")
    assert any(r["id"] == "lst_002" for r in results)

def test_size_filter_case_insensitive():
    results_upper = search_listings("jacket", size="M")
    results_lower = search_listings("jacket", size="m")
    assert ids(results_upper) == ids(results_lower)

def test_size_filter_excludes_non_matching():
    # Searching for size "S" should not return lst_004 which is size "M"
    results = search_listings("jacket", size="S")
    assert not any(r["id"] == "lst_004" for r in results)


# ── empty result scenarios ─────────────────────────────────────────────────────

def test_no_match_returns_empty_list():
    results = search_listings("designer ballgown")
    assert results == []

def test_no_match_does_not_raise():
    # Confirm empty result is a clean return, not an exception
    try:
        results = search_listings("xyzzy nonexistent item zzzfoo")
        assert results == []
    except Exception as e:
        pytest.fail(f"search_listings raised unexpectedly: {e}")

def test_price_filter_too_low_returns_empty():
    # No listings cost less than $1
    results = search_listings("vintage", max_price=1.0)
    assert results == []

def test_size_filter_no_match_returns_empty():
    results = search_listings("jacket", size="XXXL")
    assert results == []

def test_empty_description_returns_empty():
    # No keywords means nothing can score > 0
    results = search_listings("   ")
    assert results == []


# ── combined filters ───────────────────────────────────────────────────────────

def test_size_and_price_combined():
    results = search_listings("vintage", size="M", max_price=50.0)
    for r in results:
        assert r["price"] <= 50.0
        assert "m" in r["size"].lower()

def test_all_filters_too_strict_returns_empty():
    results = search_listings("ballgown", size="XXS", max_price=5.0)
    assert results == []
