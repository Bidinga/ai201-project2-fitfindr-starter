# FitFindr 🛍️

A multi-tool AI agent that helps you find secondhand pieces and figure out how to
wear them. Describe what you want in plain language; FitFindr searches the mock
listings, styles the top find against your wardrobe, and writes a shareable fit
card — short-circuiting gracefully when a step has nothing to work with.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: source .venv/Scripts/activate
pip install -r requirements.txt
```

Add a Groq API key to a `.env` file in the repo root (free key at
[console.groq.com](https://console.groq.com); already in `.gitignore`):

```
GROQ_API_KEY=your_key_here
```

> The two LLM-backed tools (`suggest_outfit`, `create_fit_card`) call Groq's
> `llama-3.3-70b-versatile`. They are written to **degrade gracefully** if the key
> is missing/invalid or the network is down — they return a useful fallback string
> instead of crashing — so the agent and the test suite still run end-to-end. For
> full-quality outfit text and varied captions, use a valid key.

## Run

```bash
python app.py            # Gradio UI at http://localhost:7860
python agent.py          # CLI: happy path + no-results path
pytest tests/            # tool tests (11 passing)
```

---

## Tool Inventory

All signatures match `tools.py` exactly.

### `search_listings(description: str, size: str | None = None, max_price: float | None = None) -> list[dict]`
- **Inputs:** `description` (str) keyword query; `size` (str | None) case-insensitive
  substring filter, e.g. `"M"` matches `"S/M"`; `max_price` (float | None) inclusive
  ceiling.
- **Output:** `list[dict]` of full listings (`id, title, description, category,
  style_tags, size, condition, price, colors, brand, platform`), sorted by relevance
  (best first), zero-overlap items dropped. Empty list when nothing matches.
- **Purpose:** Find and rank matching listings from `data/listings.json`. Pure
  Python — no LLM.

### `suggest_outfit(new_item: dict, wardrobe: dict) -> str`
- **Inputs:** `new_item` (dict) a listing; `wardrobe` (dict) with an `items` list of
  wardrobe pieces (may be empty).
- **Output:** `str` — 1–2 outfit ideas that name specific wardrobe pieces, or general
  styling advice when the wardrobe is empty.
- **Purpose:** Style the chosen item against what the user owns. Calls the LLM.

### `create_fit_card(outfit: str, new_item: dict) -> str`
- **Inputs:** `outfit` (str) the suggestion from `suggest_outfit`; `new_item` (dict)
  the listing.
- **Output:** `str` — a 2–4 sentence casual OOTD caption mentioning item, price, and
  platform once each. Varies across calls (high temperature).
- **Purpose:** Produce a shareable caption for the find. Calls the LLM.

---

## How the Planning Loop Works

`run_agent(query, wardrobe)` in `agent.py` is a staged loop where **each tool call is
gated on the previous result** — it does not call all three tools unconditionally.

1. **Parse** (`_parse_query`, regex): pull `max_price` (`under/below/max/$` + number),
   an optional `size` (`size X` or a bare `xs/s/m/l/xl` token), and keep the full
   query as `description`. Stored in `session["parsed"]`. Regex is chosen over an LLM
   call because it is deterministic, instant, and free.
2. **Search:** call `search_listings(...)`; store in `session["search_results"]`.
   - **Branch A — `results == []`:** write a tailored message into `session["error"]`
     (naming exactly what to loosen — the size filter, the price, or the keywords)
     and **return immediately.** The styling and caption tools are never reached with
     empty input.
   - **Branch B — non-empty:** set `session["selected_item"] = results[0]` and continue.
3. **Suggest:** call `suggest_outfit(selected_item, wardrobe)` → `session["outfit_suggestion"]`.
4. **Fit card:** call `create_fit_card(outfit_suggestion, selected_item)` → `session["fit_card"]`.
5. **Return** the session.

Behavior visibly changes with input: an impossible query ends after step 2 with an
error and **zero** LLM calls; a matching query runs all the way to a fit card. The
caller distinguishes the two by checking `session["error"] is None`.

---

## State Management

A single `session` dict (built by `_new_session`) is the source of truth for one
interaction and is threaded through every stage. Fields and when they are set:

| Field | Set in | Holds |
|-------|--------|-------|
| `query` | init | original text |
| `parsed` | step 1 | `{description, size, max_price}` |
| `search_results` | step 2 | list from `search_listings` |
| `selected_item` | step 2B | `search_results[0]` — fed into `suggest_outfit` |
| `wardrobe` | init | the wardrobe dict |
| `outfit_suggestion` | step 3 | string fed into `create_fit_card` |
| `fit_card` | step 4 | final caption |
| `error` | early exit | message; `None` on success |

The item found by search flows into styling and captioning **without the user
re-entering anything** — `selected_item` is the same dict object as
`search_results[0]` (verified: `session["selected_item"] is session["search_results"][0]`
→ `True`), and its output string is what `create_fit_card` receives. `app.py` reads
the finished session and maps `selected_item` / `outfit_suggestion` / `fit_card`
(or `error`) onto the three UI panels.

---

## Error Handling (per tool, with a tested example)

| Tool | Failure mode | What the agent does |
|------|-------------|----------------------|
| `search_listings` | No match | Returns `[]` (never raises); the loop sets a specific `error` and stops before styling. |
| `suggest_outfit` | Empty wardrobe / LLM down | Empty wardrobe → general advice (not an error). LLM error → caught, returns a fallback styling string. |
| `create_fit_card` | Missing/empty outfit / LLM down | Empty outfit → descriptive `⚠️` string, no LLM call. LLM error → caught, returns a simple fallback caption. |

**Concrete example (triggered in testing).** Running the impossible query through the
full agent:

```
$ python -c "from tools import search_listings; print(search_listings('designer ballgown', size='XXS', max_price=5))"
[]
```
```
=== No-results path ===
Error message: No listings matched “designer ballgown size XXS under $5”. Try
dropping the size filter (XXS), or raising your max price (currently $5), or using
broader keywords.
```
`search_listings` returns `[]` without raising, and the loop responds with a message
that names each constraint to loosen — then stops, leaving `fit_card` as `None`
rather than styling an empty result. The empty-outfit guard is likewise tested:
`create_fit_card("", item)` returns the `⚠️` string, not an exception.

---

## Stretch Feature: Retry with Loosened Constraints

When `search_listings` returns `[]`, the loop doesn't give up immediately — it
retries with filters progressively relaxed (`_search_with_retry` in `agent.py`),
and tells the user what changed via `session["adjustments"]`:

1. search as parsed → 2. drop the size filter → 3. drop the price ceiling too.

It stops at the first attempt that returns results. Keyword `description` is never
relaxed, so a nonsense query still terminates with the helpful error — retry only
loosens *filters*, it never invents matches.

```
query: "vintage graphic tee size XXS under $30"
  → no XXS match → retry without size → found "Vintage Band Tee — Faded Grey"
  → session["adjustments"] = ["removed the size filter (XXS)"]
  → UI shows: "ℹ️ No exact matches, so I removed the size filter (XXS)."
```

A truly impossible query (`"qwerty zzznotathing size XXS under $5"`) exhausts the
ladder and falls back to the original error path (`fit_card` stays `None`).

---

## Spec Reflection

**One way `planning.md` helped during implementation.** Writing the Planning Loop
section as explicit branches ("if `results == []`, set `error` and return; else set
`selected_item` and continue") meant `run_agent` was almost a transcription of the
spec — the early-return branch was decided on paper, so there was no point where the
code accidentally called `suggest_outfit` on an empty list. The diagram's error arrow
mapped directly to the one `return session` inside Branch A.

**One divergence, and why.** The spec/walkthrough used the Y2K butterfly baby tee as
the top result for "vintage graphic tee," but the actual keyword scorer ranks the
"Vintage Band Tee — Faded Grey" first (it hits `vintage`, `band tee`, `graphic tee`
in title + tags). I left the scorer as-is rather than hand-tuning toward the example,
because the result is genuinely a better match for the query — the divergence is the
ranking doing its job, not a bug. I also added graceful LLM fallbacks beyond the spec
so a missing/invalid key degrades the output instead of crashing the agent.

---

## AI Usage

**1. `search_listings` implementation.** I gave Claude the Tool 1 block from
`planning.md` (inputs/types, the relevance-scoring + drop-zero-score requirement, and
"return `[]`, never raise"). It produced a working filter+score function. I **revised**
it in two ways: it originally treated every field equally, so I added field weighting
(title 3 / tags 2 / body 1) so the most intentional signal ranks highest, and I added
a `_STOPWORDS` list + numeric/short-token stripping so query filler like "under",
"size", and "$30" couldn't inflate relevance. Verified against the three pytest cases
(results > 0, impossible → `[]`, price filter holds).

**2. Planning loop + state.** I gave Claude the Architecture diagram and the Planning
Loop and State Management sections and asked for `run_agent`. I **overrode** the first
draft, which called all three tools and only checked for emptiness at the end — I
restructured it to return early inside the empty-search branch (so the LLM tools are
never reached with empty input) and to make the no-results message name exactly which
constraints to loosen rather than a generic "no results found." Verified by confirming
`selected_item is search_results[0]` on the happy path and `error` set / `fit_card`
`None` on the no-results path.

---

## Project Layout

```
data/            listings.json (40 mock listings) + wardrobe_schema.json
utils/           data_loader.py — load_listings / get_example_wardrobe / get_empty_wardrobe
tools.py         the 3 tools
agent.py         run_agent planning loop + query parsing
app.py           Gradio UI (handle_query)
tests/           test_tools.py (pytest)
planning.md      the spec, written before implementation
```
