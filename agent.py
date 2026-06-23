"""
agent.py

The FitFindr planning loop. Orchestrates the three tools in response to a
natural language user query, passing state between them via a session dict.

Complete tools.py and test each tool in isolation before implementing this file.

Usage (once implemented):
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import re

from tools import search_listings, suggest_outfit, create_fit_card


# ── query parsing ─────────────────────────────────────────────────────────────

# Recognized clothing-size tokens for the "bare size" fallback (e.g. "in M").
_SIZE_TOKENS = {"xxs", "xs", "s", "m", "l", "xl", "xxl", "xxxl"}


def _parse_query(query: str) -> dict:
    """
    Extract search parameters from the raw natural-language query using regex.

    Chosen over an LLM call because it is deterministic, instant, and free — the
    only thing we need is a price ceiling, an optional size, and keywords.

    Returns a dict: {"description": str, "size": str | None, "max_price": float | None}.
    """
    text = query or ""
    lower = text.lower()

    # Price: "under/below/less than/max/up to $30" or a bare "$30".
    max_price = None
    m = re.search(r"(?:under|below|less than|max|up to|<=?)\s*\$?\s*(\d+(?:\.\d+)?)", lower)
    if not m:
        m = re.search(r"\$\s*(\d+(?:\.\d+)?)", lower)
    if m:
        max_price = float(m.group(1))

    # Size: explicit "size M" / "size 8", else a bare known size token.
    size = None
    m = re.search(r"\bsize\s+([a-z0-9/]+)", lower)
    if m:
        size = m.group(1).upper()
    else:
        for tok in re.findall(r"[a-z]+", lower):
            if tok in _SIZE_TOKENS:
                size = tok.upper()
                break

    # Description: the original query (search_listings tokenizes + drops filler).
    return {"description": text.strip(), "size": size, "max_price": max_price}


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.

    The session dict is the single source of truth for everything that happens
    during a run — it stores the original query, parsed parameters, tool results,
    and any error that caused early termination.

    You may add fields to this dict as needed for your implementation.
    """
    return {
        "query": query,              # original user query
        "parsed": {},                # extracted description / size / max_price
        "search_results": [],        # list of matching listing dicts
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "adjustments": [],           # filters loosened by retry, for the user
        "error": None,               # set if the interaction ended early
    }


# ── search with retry (stretch: loosen constraints) ───────────────────────────

def _search_with_retry(parsed: dict) -> tuple[list[dict], list[str]]:
    """
    Run search_listings, progressively loosening filters until something matches.

    Returns (results, relaxations) where `relaxations` describes what was dropped
    (empty if the first attempt already worked). Keyword `description` is never
    relaxed — retry only loosens the size/price filters, it never invents matches.
    """
    description = parsed["description"]
    size = parsed["size"]
    max_price = parsed["max_price"]

    # Attempt 1: exactly as parsed.
    results = search_listings(description, size, max_price)
    if results:
        return results, []

    relaxations: list[str] = []

    # Attempt 2: drop the size filter (if one was set).
    if size is not None:
        relaxations.append(f"removed the size filter ({size})")
        results = search_listings(description, None, max_price)
        if results:
            return results, relaxations

    # Attempt 3: drop the price ceiling too (if one was set).
    if max_price is not None:
        relaxations.append(f"removed the ${max_price:g} price limit")
        results = search_listings(description, None, None)
        if results:
            return results, relaxations

    # Nothing matched even fully relaxed — keywords match no listing.
    return [], relaxations


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single
    user interaction and returns the completed session dict.

    Args:
        query:    Natural language user request
                  (e.g., "vintage graphic tee under $30, size M")
        wardrobe: User's wardrobe dict — use get_example_wardrobe() or
                  get_empty_wardrobe() from utils/data_loader.py

    Returns:
        The session dict after the interaction completes. Check session["error"]
        first — if it is not None, the interaction ended early and the other
        output fields (outfit_suggestion, fit_card) will be None.

    TODO — implement this function using the planning loop you designed in planning.md:

        Step 1: Initialize the session with _new_session().

        Step 2: Parse the user's query to extract a description, size, and
                max_price. You can use regex, string splitting, or ask the LLM
                to parse it — document your choice in planning.md.
                Store the result in session["parsed"].

        Step 3: Call search_listings() with the parsed parameters.
                Store results in session["search_results"].
                If no results: set session["error"] to a helpful message and
                return the session early. Do NOT proceed to suggest_outfit
                with empty input.

        Step 4: Select the item to use (e.g., the top result).
                Store it in session["selected_item"].

        Step 5: Call suggest_outfit() with the selected item and wardrobe.
                Store the result in session["outfit_suggestion"].

        Step 6: Call create_fit_card() with the outfit suggestion and selected item.
                Store the result in session["fit_card"].

        Step 7: Return the session.

    Before writing code, complete the Planning Loop and State Management sections
    of planning.md — your implementation should match what you described there.
    """
    # Step 1: fresh session — the single source of truth for this interaction.
    session = _new_session(query, wardrobe)

    # Step 2: parse the query into search parameters (regex; see _parse_query).
    parsed = _parse_query(query)
    session["parsed"] = parsed

    # Step 3: search. This is the gate that decides whether the rest runs.
    # Stretch: if the exact filters match nothing, retry with them loosened.
    results, relaxations = _search_with_retry(parsed)
    session["search_results"] = results
    session["adjustments"] = relaxations

    # Branch A — nothing matched even after loosening: communicate and STOP.
    # We do not call suggest_outfit / create_fit_card with empty input.
    if not results:
        loosen = []
        if parsed["size"]:
            loosen.append(f"dropping the size filter ({parsed['size']})")
        if parsed["max_price"] is not None:
            loosen.append(f"raising your max price (currently ${parsed['max_price']:g})")
        loosen.append("using broader keywords")
        session["error"] = (
            f"No listings matched “{query.strip()}”. Try "
            + ", or ".join(loosen)
            + "."
        )
        return session

    # Branch B — we have matches: select the top-ranked item and continue.
    session["selected_item"] = results[0]

    # Step 5: style the selected item against the wardrobe.
    session["outfit_suggestion"] = suggest_outfit(session["selected_item"], wardrobe)

    # Step 6: turn the outfit into a shareable fit card.
    session["fit_card"] = create_fit_card(
        session["outfit_suggestion"], session["selected_item"]
    )

    # Step 7: return the completed session.
    return session


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        print(f"Found: {session['selected_item']['title']}")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error message: {session2['error']}")
