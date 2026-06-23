"""
tools.py

The three required FitFindr tools. Each tool is a standalone function that
can be called and tested independently before being wired into the agent loop.

Complete and test each tool before moving to agent.py.

Tools:
    search_listings(description, size, max_price)  → list[dict]
    suggest_outfit(new_item, wardrobe)              → str
    create_fit_card(outfit, new_item)               → str
"""

import os
import re

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()

MODEL = "llama-3.3-70b-versatile"


# ── Groq client ───────────────────────────────────────────────────────────────

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


def _chat(prompt: str, temperature: float = 0.7, max_tokens: int = 400) -> str:
    """
    Send a single-turn prompt to the LLM and return the text response.

    Raises on failure — callers (suggest_outfit / create_fit_card) wrap this in
    try/except so the agent degrades gracefully instead of crashing.
    """
    client = _get_groq_client()
    resp = client.chat.completions.create(
        model=MODEL,
        messages=[{"role": "user", "content": prompt}],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()


# Words that carry no search signal — stripped before keyword scoring so query
# filler ("under", "size", "looking") does not inflate relevance.
_STOPWORDS = {
    "a", "an", "the", "and", "or", "for", "with", "in", "on", "of", "to", "my",
    "i", "im", "i'm", "me", "looking", "want", "need", "find", "something",
    "under", "below", "less", "than", "max", "around", "about", "size", "sized",
    "cheap", "good", "nice", "some", "any", "thats", "that", "this", "what",
    "out", "there", "how", "would", "style", "wear", "wearing", "mostly", "up",
    "dollars", "dollar", "price", "priced", "buy", "get", "thrift", "thrifted",
}


def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-alphanumerics, drop stopwords and pure numbers."""
    tokens = re.findall(r"[a-z0-9']+", (text or "").lower())
    return [t for t in tokens if t not in _STOPWORDS and not t.isdigit() and len(t) > 1]


# ── Tool 1: search_listings ───────────────────────────────────────────────────

def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Args:
        description: Keywords describing what the user is looking for
                     (e.g., "vintage graphic tee").
        size:        Size string to filter by, or None to skip size filtering.
                     Matching is case-insensitive (e.g., "M" matches "S/M").
        max_price:   Maximum price (inclusive), or None to skip price filtering.

    Returns:
        A list of matching listing dicts, sorted by relevance (best match first).
        Returns an empty list if nothing matches — does NOT raise an exception.

    Each listing dict has the following fields:
        id, title, description, category, style_tags (list), size,
        condition, price (float), colors (list), brand, platform

    TODO:
        1. Load all listings with load_listings().
        2. Filter by max_price and size (if provided).
        3. Score each remaining listing by keyword overlap with `description`.
        4. Drop any listings with a score of 0 (no relevant matches).
        5. Sort by score, highest first, and return the listing dicts.

    Before writing code, fill in the Tool 1 section of planning.md.
    """
    listings = load_listings()
    query_tokens = _tokenize(description)

    scored: list[tuple[int, float, dict]] = []
    for item in listings:
        # 1. Hard filters — price ceiling and size.
        if max_price is not None and item.get("price", 0) > max_price:
            continue
        if size is not None:
            item_size = (item.get("size") or "").lower()
            if size.strip().lower() not in item_size:
                continue

        # 2. Relevance score: keyword overlap across the searchable fields.
        #    Title and style_tags are weighted more heavily than the long
        #    description because they are the most intentional signal.
        title_tokens = set(_tokenize(item.get("title", "")))
        tag_tokens = set(_tokenize(" ".join(item.get("style_tags", []))))
        tag_tokens |= {item.get("category", "").lower()}
        body_tokens = set(_tokenize(item.get("description", "")))
        body_tokens |= set(_tokenize(" ".join(item.get("colors", []))))
        body_tokens |= set(_tokenize(item.get("brand") or ""))

        score = 0
        for tok in query_tokens:
            if tok in title_tokens:
                score += 3
            elif tok in tag_tokens:
                score += 2
            elif tok in body_tokens:
                score += 1

        # 3. Drop listings with no keyword overlap at all.
        if score > 0:
            scored.append((score, item.get("price", 0.0), item))

    # 4. Sort by score (desc), then by price (asc) as a tiebreak so cheaper
    #    equally-relevant finds surface first. Return the listing dicts only.
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [item for _, _, item in scored]


# ── Tool 2: suggest_outfit ────────────────────────────────────────────────────

def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1–2 complete outfits.

    Args:
        new_item: A listing dict (the item the user is considering buying).
        wardrobe: A wardrobe dict with an 'items' key containing a list of
                  wardrobe item dicts. May be empty — handle this gracefully.

    Returns:
        A non-empty string with outfit suggestions.
        If the wardrobe is empty, offer general styling advice for the item
        rather than raising an exception or returning an empty string.

    TODO:
        1. Check whether wardrobe['items'] is empty.
        2. If empty: call the LLM with a prompt for general styling ideas
           (what kinds of items pair well, what vibe it suits, etc.).
        3. If not empty: format the wardrobe items into a prompt and ask
           the LLM to suggest specific outfit combinations using the new item
           and named pieces from the wardrobe.
        4. Return the LLM's response as a string.

    Before writing code, fill in the Tool 2 section of planning.md.
    """
    item_line = (
        f"{new_item.get('title', 'this piece')} "
        f"(category: {new_item.get('category', 'unknown')}; "
        f"colors: {', '.join(new_item.get('colors', [])) or 'n/a'}; "
        f"style: {', '.join(new_item.get('style_tags', [])) or 'n/a'})"
    )

    items = wardrobe.get("items", []) if isinstance(wardrobe, dict) else []

    if not items:
        # Empty wardrobe → general styling advice (not an error).
        prompt = (
            "You are a thoughtful personal stylist. A shopper is considering this "
            f"secondhand item:\n  {item_line}\n\n"
            "They have not entered any wardrobe yet, so give GENERAL styling advice: "
            "what kinds of pieces pair well with it, what occasions/vibe it suits, "
            "and one or two concrete outfit ideas a typical closet could pull off. "
            "Keep it to 3-4 sentences, warm and specific. Do not invent items they own."
        )
    else:
        wardrobe_lines = "\n".join(
            f"  - {it.get('name', 'item')} ({it.get('category', '?')};"
            f" {', '.join(it.get('colors', [])) or 'n/a'})"
            for it in items
        )
        prompt = (
            "You are a thoughtful personal stylist. A shopper just found this "
            f"secondhand item:\n  {item_line}\n\n"
            f"Here is what they already own:\n{wardrobe_lines}\n\n"
            "Suggest 1-2 complete outfits that pair the new item with SPECIFIC pieces "
            "from their wardrobe, referring to those pieces by name. Mention shoes and "
            "a layer or accessory where it makes sense, and add one quick styling tip "
            "(cuff, tuck, layer). Keep it to 3-5 sentences, concrete and wearable."
        )

    try:
        result = _chat(prompt, temperature=0.7, max_tokens=350)
        if result:
            return result
        raise ValueError("empty LLM response")
    except Exception:
        # LLM unavailable (bad key, network, timeout) — degrade gracefully so the
        # agent stays useful instead of crashing.
        tags = ", ".join(new_item.get("style_tags", [])) or "versatile"
        return (
            f"(Styling assistant is unavailable right now, so here's a quick take.) "
            f"The {new_item.get('title', 'piece')} leans {tags}. Build around it with "
            f"simple basics in a neutral color, add a contrasting layer, and finish "
            f"with shoes that match the vibe — keep the rest of the outfit calm so the "
            f"piece stays the focal point."
        )


# ── Tool 3: create_fit_card ───────────────────────────────────────────────────

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable outfit caption for the thrifted find.

    Args:
        outfit:   The outfit suggestion string from suggest_outfit().
        new_item: The listing dict for the thrifted item.

    Returns:
        A 2–4 sentence string usable as an Instagram/TikTok caption.
        If outfit is empty or missing, return a descriptive error message
        string — do NOT raise an exception.

    The caption should:
    - Feel casual and authentic (like a real OOTD post, not a product description)
    - Mention the item name, price, and platform naturally (once each)
    - Capture the outfit vibe in specific terms
    - Sound different each time for different inputs (use higher LLM temperature)

    TODO:
        1. Guard against an empty or whitespace-only outfit string.
        2. Build a prompt that gives the LLM the item details and the outfit,
           and asks for a caption matching the style guidelines above.
        3. Call the LLM and return the response.

    Before writing code, fill in the Tool 3 section of planning.md.
    """
    # 1. Guard: no outfit → descriptive error string, never an exception.
    if not outfit or not outfit.strip():
        return (
            "⚠️ Can't write a fit card without an outfit suggestion — the styling "
            "step didn't produce one. Try the search again or pick a different item."
        )

    title = new_item.get("title", "this piece")
    price = new_item.get("price")
    price_str = f"${price:g}" if isinstance(price, (int, float)) else "a steal"
    platform = new_item.get("platform", "secondhand")

    prompt = (
        "Write a short, casual OOTD-style social caption (2-4 sentences) for a "
        "thrifted find. It should read like a real person posting, NOT a product "
        "description. Be specific about the vibe.\n\n"
        f"Item: {title}\n"
        f"Price: {price_str}\n"
        f"Platform: {platform}\n"
        f"Outfit it's styled in: {outfit}\n\n"
        f"Mention the item, the price ({price_str}), and the platform ({platform}) "
        "once each, naturally. Lowercase-casual is fine. 1-2 emoji max. "
        "Just return the caption text."
    )

    try:
        # Higher temperature so repeated calls / different inputs vary.
        result = _chat(prompt, temperature=0.95, max_tokens=180)
        if result:
            return result
        raise ValueError("empty LLM response")
    except Exception:
        # LLM unavailable — fall back to a simple caption from the item fields.
        return (
            f"thrifted this {title.lower()} off {platform} for {price_str} and "
            f"i'm obsessed — styled it exactly how i wanted. full look soon ✨"
        )
