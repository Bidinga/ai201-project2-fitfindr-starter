"""
tests/test_tools.py

Tool-level tests, with at least one test per failure mode. Run with:
    pytest tests/

The LLM-backed tools (suggest_outfit, create_fit_card) are written to degrade
gracefully if the LLM is unavailable, so these tests assert on the contract
(non-empty string, no exception) rather than on exact wording — they pass whether
or not a live Groq key is configured.
"""

from tools import search_listings, suggest_outfit, create_fit_card
from agent import _search_with_retry
from utils.data_loader import get_example_wardrobe, get_empty_wardrobe


# ── search_listings ─────────────────────────────────────────────────────────

def test_search_returns_results():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    assert isinstance(results, list)
    assert len(results) > 0


def test_search_empty_results():
    # Impossible query — must return an empty list, not raise.
    results = search_listings("designer ballgown", size="XXS", max_price=5)
    assert results == []


def test_search_price_filter():
    results = search_listings("jacket", size=None, max_price=10)
    assert all(item["price"] <= 10 for item in results)


def test_search_results_sorted_by_relevance():
    # Score is non-increasing — first result is the best match.
    results = search_listings("vintage denim jeans", size=None, max_price=None)
    assert len(results) > 1
    # Top result should be a denim/jeans item.
    top = results[0]
    text = (top["title"] + " " + " ".join(top["style_tags"])).lower()
    assert "denim" in text or "jeans" in text


# ── suggest_outfit ──────────────────────────────────────────────────────────

def test_suggest_outfit_with_wardrobe():
    item = search_listings("vintage graphic tee", size=None, max_price=50)[0]
    out = suggest_outfit(item, get_example_wardrobe())
    assert isinstance(out, str) and len(out.strip()) > 0


def test_suggest_outfit_empty_wardrobe():
    # Empty wardrobe is a handled case, not a crash — must return useful text.
    item = search_listings("vintage graphic tee", size=None, max_price=50)[0]
    out = suggest_outfit(item, get_empty_wardrobe())
    assert isinstance(out, str) and len(out.strip()) > 0


# ── create_fit_card ─────────────────────────────────────────────────────────

def test_create_fit_card_empty_outfit():
    # Missing outfit → descriptive error STRING, never an exception.
    item = search_listings("vintage graphic tee", size=None, max_price=50)[0]
    card = create_fit_card("", item)
    assert isinstance(card, str)
    assert card.startswith("⚠️")


def test_create_fit_card_returns_string():
    item = search_listings("vintage graphic tee", size=None, max_price=50)[0]
    card = create_fit_card("paired with baggy jeans and chunky sneakers", item)
    assert isinstance(card, str) and len(card.strip()) > 0


# ── retry-with-loosened-constraints (stretch) ────────────────────────────────

def test_retry_no_relaxation_when_first_search_works():
    parsed = {"description": "vintage graphic tee", "size": None, "max_price": 50}
    results, relaxations = _search_with_retry(parsed)
    assert len(results) > 0
    assert relaxations == []


def test_retry_loosens_impossible_size():
    # A real graphic tee exists, but not in size "ZZ" — retry should drop the
    # size filter, return results, and report what it changed.
    parsed = {"description": "vintage graphic tee", "size": "ZZ", "max_price": 50}
    results, relaxations = _search_with_retry(parsed)
    assert len(results) > 0
    assert any("size" in r for r in relaxations)


def test_retry_gives_up_on_nonsense_keywords():
    # Keywords that match no listing — retry can't invent matches.
    parsed = {"description": "qwerty zzzznotathing", "size": "XXS", "max_price": 5}
    results, relaxations = _search_with_retry(parsed)
    assert results == []
