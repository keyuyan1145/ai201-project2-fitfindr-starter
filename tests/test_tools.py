"""
Tests for search_listings(), suggest_outfit(), and create_fit_card() in tools.py.

Run from the project root:
    pytest tests/test_tools.py -v
"""

import pytest
from unittest.mock import MagicMock, patch
from tools import search_listings, suggest_outfit, create_fit_card


# ── shared fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def sample_item():
    return {
        "id": "lst_006",
        "title": "Graphic Tee — 2003 Tour Bootleg Style",
        "category": "tops",
        "style_tags": ["graphic tee", "vintage", "grunge", "streetwear"],
        "colors": ["black"],
        "description": "Vintage-style bootleg tee with faded graphic.",
        "price": 24.0,
        "platform": "depop",
    }

@pytest.fixture
def example_wardrobe():
    return {
        "items": [
            {
                "id": "w_001",
                "name": "Baggy straight-leg jeans, dark wash",
                "category": "bottoms",
                "colors": ["dark blue", "indigo"],
                "style_tags": ["denim", "streetwear", "baggy"],
            },
            {
                "id": "w_007",
                "name": "Chunky white sneakers",
                "category": "shoes",
                "colors": ["white"],
                "style_tags": ["sneakers", "chunky", "streetwear"],
            },
        ]
    }

@pytest.fixture
def empty_wardrobe():
    return {"items": []}

def _mock_client(content="Here is your outfit suggestion."):
    mock_response = MagicMock()
    mock_response.choices[0].message.content = content
    client = MagicMock()
    client.chat.completions.create.return_value = mock_response
    return client


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


# ── suggest_outfit: happy paths ────────────────────────────────────────────────

def test_suggest_outfit_with_wardrobe_returns_string(sample_item, example_wardrobe):
    with patch("tools._get_groq_client", return_value=_mock_client()):
        result = suggest_outfit(sample_item, example_wardrobe)
    assert isinstance(result, str)
    assert len(result) > 0

def test_suggest_outfit_empty_wardrobe_returns_string(sample_item, empty_wardrobe):
    with patch("tools._get_groq_client", return_value=_mock_client()):
        result = suggest_outfit(sample_item, empty_wardrobe)
    assert isinstance(result, str)
    assert len(result) > 0

def test_suggest_outfit_returns_llm_content(sample_item, example_wardrobe):
    with patch("tools._get_groq_client", return_value=_mock_client("Wear it with baggy jeans.")):
        result = suggest_outfit(sample_item, example_wardrobe)
    assert result == "Wear it with baggy jeans."


# ── suggest_outfit: prompt content ────────────────────────────────────────────

def test_suggest_outfit_prompt_includes_item_title(sample_item, example_wardrobe):
    client = _mock_client()
    with patch("tools._get_groq_client", return_value=client):
        suggest_outfit(sample_item, example_wardrobe)
    prompt = client.chat.completions.create.call_args[1]["messages"][0]["content"]
    assert sample_item["title"] in prompt

def test_suggest_outfit_prompt_includes_wardrobe_items(sample_item, example_wardrobe):
    client = _mock_client()
    with patch("tools._get_groq_client", return_value=client):
        suggest_outfit(sample_item, example_wardrobe)
    prompt = client.chat.completions.create.call_args[1]["messages"][0]["content"]
    for item in example_wardrobe["items"]:
        assert item["name"] in prompt

def test_suggest_outfit_empty_wardrobe_prompt_excludes_wardrobe_items(sample_item, empty_wardrobe):
    client = _mock_client()
    with patch("tools._get_groq_client", return_value=client):
        suggest_outfit(sample_item, empty_wardrobe)
    prompt = client.chat.completions.create.call_args[1]["messages"][0]["content"]
    # Empty wardrobe prompt should not reference specific wardrobe pieces
    assert "They already own" not in prompt

def test_suggest_outfit_empty_wardrobe_prompt_asks_for_general_advice(sample_item, empty_wardrobe):
    client = _mock_client()
    with patch("tools._get_groq_client", return_value=client):
        suggest_outfit(sample_item, empty_wardrobe)
    prompt = client.chat.completions.create.call_args[1]["messages"][0]["content"]
    assert "don't have a specific wardrobe" in prompt


# ── suggest_outfit: error and edge cases ──────────────────────────────────────

def test_suggest_outfit_llm_exception_returns_empty_string(sample_item, example_wardrobe):
    client = MagicMock()
    client.chat.completions.create.side_effect = Exception("API unavailable")
    with patch("tools._get_groq_client", return_value=client):
        result = suggest_outfit(sample_item, example_wardrobe)
    assert result == ""

def test_suggest_outfit_llm_returns_empty_content_returns_empty_string(sample_item, example_wardrobe):
    with patch("tools._get_groq_client", return_value=_mock_client("")):
        result = suggest_outfit(sample_item, example_wardrobe)
    assert result == ""

def test_suggest_outfit_llm_returns_whitespace_only_returns_empty_string(sample_item, example_wardrobe):
    with patch("tools._get_groq_client", return_value=_mock_client("   \n  ")):
        result = suggest_outfit(sample_item, example_wardrobe)
    assert result == ""

def test_suggest_outfit_missing_item_fields_does_not_raise(empty_wardrobe):
    # Item with no optional fields should not crash
    minimal_item = {"title": "Plain Tee"}
    with patch("tools._get_groq_client", return_value=_mock_client("Some advice.")):
        result = suggest_outfit(minimal_item, empty_wardrobe)
    assert isinstance(result, str)

def test_suggest_outfit_does_not_raise_on_llm_failure(sample_item, example_wardrobe):
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("timeout")
    with patch("tools._get_groq_client", return_value=client):
        try:
            suggest_outfit(sample_item, example_wardrobe)
        except Exception as e:
            pytest.fail(f"suggest_outfit raised unexpectedly: {e}")


# ── create_fit_card: happy paths ───────────────────────────────────────────────

OUTFIT = "Graphic tee tucked into baggy dark-wash jeans with chunky white sneakers."

def test_create_fit_card_returns_string(sample_item):
    with patch("tools._get_groq_client", return_value=_mock_client("Great thrift find!")):
        result = create_fit_card(OUTFIT, sample_item)
    assert isinstance(result, str)
    assert len(result) > 0

def test_create_fit_card_returns_llm_content(sample_item):
    with patch("tools._get_groq_client", return_value=_mock_client("Thrifted this gem on depop.")):
        result = create_fit_card(OUTFIT, sample_item)
    assert result == "Thrifted this gem on depop."


# ── create_fit_card: prompt content ───────────────────────────────────────────

def test_create_fit_card_prompt_includes_item_title(sample_item):
    client = _mock_client()
    with patch("tools._get_groq_client", return_value=client):
        create_fit_card(OUTFIT, sample_item)
    prompt = client.chat.completions.create.call_args[1]["messages"][0]["content"]
    assert sample_item["title"] in prompt

def test_create_fit_card_prompt_includes_price(sample_item):
    client = _mock_client()
    with patch("tools._get_groq_client", return_value=client):
        create_fit_card(OUTFIT, sample_item)
    prompt = client.chat.completions.create.call_args[1]["messages"][0]["content"]
    assert "24.00" in prompt

def test_create_fit_card_prompt_includes_platform(sample_item):
    client = _mock_client()
    with patch("tools._get_groq_client", return_value=client):
        create_fit_card(OUTFIT, sample_item)
    prompt = client.chat.completions.create.call_args[1]["messages"][0]["content"]
    assert sample_item["platform"] in prompt

def test_create_fit_card_prompt_includes_outfit(sample_item):
    client = _mock_client()
    with patch("tools._get_groq_client", return_value=client):
        create_fit_card(OUTFIT, sample_item)
    prompt = client.chat.completions.create.call_args[1]["messages"][0]["content"]
    assert OUTFIT in prompt


# ── create_fit_card: empty outfit guard ───────────────────────────────────────

def test_create_fit_card_empty_outfit_returns_error_string(sample_item):
    result = create_fit_card("", sample_item)
    assert "Cannot create a fit card" in result

def test_create_fit_card_whitespace_outfit_returns_error_string(sample_item):
    result = create_fit_card("   \n  ", sample_item)
    assert "Cannot create a fit card" in result

def test_create_fit_card_empty_outfit_does_not_call_llm(sample_item):
    client = _mock_client()
    with patch("tools._get_groq_client", return_value=client):
        create_fit_card("", sample_item)
    client.chat.completions.create.assert_not_called()


# ── create_fit_card: error and edge cases ─────────────────────────────────────

def test_create_fit_card_llm_exception_returns_empty_string(sample_item):
    client = MagicMock()
    client.chat.completions.create.side_effect = Exception("API unavailable")
    with patch("tools._get_groq_client", return_value=client):
        result = create_fit_card(OUTFIT, sample_item)
    assert result == ""

def test_create_fit_card_llm_returns_empty_content_returns_empty_string(sample_item):
    with patch("tools._get_groq_client", return_value=_mock_client("")):
        result = create_fit_card(OUTFIT, sample_item)
    assert result == ""

def test_create_fit_card_llm_returns_whitespace_only_returns_empty_string(sample_item):
    with patch("tools._get_groq_client", return_value=_mock_client("  \n  ")):
        result = create_fit_card(OUTFIT, sample_item)
    assert result == ""

def test_create_fit_card_missing_price_does_not_crash(sample_item):
    item_no_price = {k: v for k, v in sample_item.items() if k != "price"}
    with patch("tools._get_groq_client", return_value=_mock_client("Nice fit!")):
        result = create_fit_card(OUTFIT, item_no_price)
    assert isinstance(result, str)

def test_create_fit_card_missing_platform_does_not_crash(sample_item):
    item_no_platform = {k: v for k, v in sample_item.items() if k != "platform"}
    with patch("tools._get_groq_client", return_value=_mock_client("Nice fit!")):
        result = create_fit_card(OUTFIT, item_no_platform)
    assert isinstance(result, str)

def test_create_fit_card_does_not_raise_on_llm_failure(sample_item):
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("timeout")
    with patch("tools._get_groq_client", return_value=client):
        try:
            create_fit_card(OUTFIT, sample_item)
        except Exception as e:
            pytest.fail(f"create_fit_card raised unexpectedly: {e}")
