# FitFindr — planning.md

> Written before implementation. This spec is what I used to direct the AI tool
> (Claude) that generated the first drafts of each function. The agent diagram
> and the per-tool blocks below were pasted in verbatim as prompts.

**What FitFindr needs to do (in my own words):** FitFindr takes one natural
language thrifting request and turns it into a styled, shareable find. It parses
the request into search parameters and calls `search_listings` first; if that
returns matches it picks the top one and calls `suggest_outfit` to style it
against the user's wardrobe, then `create_fit_card` to write a caption. Each tool
guards its own failure mode — if `search_listings` finds nothing the agent stops
and tells the user what to loosen instead of styling an empty result; if the
wardrobe is empty `suggest_outfit` gives general advice; if the outfit string is
empty `create_fit_card` returns an error message instead of crashing.

---

## Tools

### Tool 1: search_listings

**What it does:**
Searches the 40-item mock listings dataset and returns the listings that match a
keyword description, optionally filtered by size and a price ceiling, ranked so
the most relevant item is first.

**Input parameters:**
- `description` (str): free-text keywords describing the wanted item, e.g.
  `"vintage graphic tee"`. Tokenized and scored against each listing.
- `size` (str | None): size to filter by, case-insensitive substring match
  (`"M"` matches `"S/M"`). `None` skips the size filter.
- `max_price` (float | None): inclusive price ceiling. `None` skips the price filter.

**What it returns:**
A `list[dict]`. Each dict is a full listing with the fields `id, title,
description, category, style_tags (list), size, condition, price (float),
colors (list), brand, platform`. The list is sorted by relevance score (highest
first), listings with a score of 0 are dropped, and the list is empty when
nothing matches. Never raises.

**What happens if it fails or returns nothing:**
Returns `[]` (never an exception). The planning loop detects the empty list,
writes a helpful message into `session["error"]` naming what to loosen (raise the
price, drop the size, use broader words) and returns early without calling the
downstream tools.

---

### Tool 2: suggest_outfit

**What it does:**
Given the chosen listing and the user's wardrobe, asks the LLM to compose 1–2
complete outfits that pair the new item with named pieces the user already owns.

**Input parameters:**
- `new_item` (dict): the selected listing dict from `search_listings`.
- `wardrobe` (dict): a wardrobe dict with an `items` key holding a list of
  wardrobe-item dicts (`name, category, colors, style_tags, notes`). May be empty.

**What it returns:**
A non-empty `str` — 1–2 outfit suggestions in plain prose, referencing wardrobe
pieces by name when the wardrobe is non-empty, or general styling advice (what
kinds of pieces pair well, what vibe it suits) when the wardrobe is empty.

**What happens if it fails or returns nothing:**
Empty wardrobe is not an error — it routes to the general-advice prompt. If the
LLM call itself fails (bad key, network, timeout), the function catches the
exception and returns a graceful fallback string with generic styling guidance
for the item's category and style tags, so the agent stays useful.

---

### Tool 3: create_fit_card

**What it does:**
Turns the outfit suggestion and the item into a short, casual caption — the kind
of thing someone posts with an OOTD photo — using a higher temperature so repeated
calls and different inputs produce different captions.

**Input parameters:**
- `outfit` (str): the outfit suggestion string from `suggest_outfit`.
- `new_item` (dict): the selected listing dict (for name, price, platform).

**What it returns:**
A 2–4 sentence `str` caption that mentions the item name, price, and platform once
each and captures the vibe. Different inputs / repeated calls produce different text.

**What happens if it fails or returns nothing:**
If `outfit` is empty or whitespace-only, returns the descriptive error string
`"⚠️ Can't write a fit card without an outfit suggestion..."` immediately — no LLM
call, no exception. If the LLM call fails, it catches the exception and returns a
short fallback caption built from the item fields.

---

### Additional Tools (if any)

None — the third stretch feature below is loop logic, not a new tool.

---

## Stretch Feature: Retry with Loosened Constraints

**Updated before implementing (per the assignment).**

**What it does:** When `search_listings` returns `[]`, the planning loop does not
immediately give up. It retries the search with constraints progressively relaxed,
and tells the user exactly what it changed.

**Where it lives:** A helper `_search_with_retry(parsed)` in `agent.py` that wraps
`search_listings`. The tool signature does not change.

**Retry ladder (stops at the first attempt that returns results):**
1. As parsed: `(description, size, max_price)`.
2. Drop the size filter: `(description, None, max_price)` — only if a size was set.
3. Drop the price ceiling too: `(description, None, None)` — only if a price was set.

**What it returns:** `(results, relaxations)` where `relaxations` is a list of
human-readable strings describing what was loosened (e.g. `["removed the size
filter (M)", "removed the $20 price limit"]`). Empty list when the first attempt
already worked.

**How the loop uses it:**
- If results come back after relaxing, the agent proceeds normally **and** records
  the relaxations in `session["adjustments"]` so the UI can show a note:
  "No exact matches, so I removed the size filter (M) to find this."
- If even the fully-relaxed search returns `[]` (the keywords match nothing at
  all), fall back to the original behavior: set `session["error"]` and return early.

**Failure mode:** unchanged — a truly impossible keyword query still terminates
with the helpful error message; retry only loosens the *filters*, never invents
matches.

---

## Planning Loop

**How does the agent decide which tool to call next?**

The loop is a staged sequence where each stage is gated on the previous stage's
result stored in the session dict — the agent does **not** call all three tools
unconditionally:

1. **Parse.** Extract `description`, `size`, `max_price` from the raw query with
   regex (price: `under/below/max/$` + number; size: `size X` or a bare
   `xs/s/m/l/xl` token). Store in `session["parsed"]`.
2. **Search.** Call `search_listings(description, size, max_price)`; store the
   list in `session["search_results"]`.
   - **Branch A — `results == []`:** set `session["error"]` to a message naming
     what to loosen and **return the session immediately.** `outfit_suggestion`
     and `fit_card` stay `None`. (This is the visible conditional branch — empty
     search short-circuits the rest of the loop.)
   - **Branch B — `results` non-empty:** set
     `session["selected_item"] = results[0]` and continue.
3. **Suggest.** Call `suggest_outfit(selected_item, wardrobe)`; store the string in
   `session["outfit_suggestion"]`.
4. **Fit card.** Call `create_fit_card(outfit_suggestion, selected_item)`; store in
   `session["fit_card"]`.
5. **Return** the completed session.

The agent's behavior changes with input: an impossible query terminates after step
2 with an error and no LLM calls; a matching query runs all the way to a fit card.
A caller can tell which path ran by checking whether `session["error"]` is `None`.

---

## State Management

**How does information from one tool get passed to the next?**

A single `session` dict (created by `_new_session`) is the one source of truth for
the interaction. It is created once per `run_agent` call and threaded through every
stage. Fields:

- `query` — original text (set at init)
- `parsed` — `{description, size, max_price}` (set in step 1)
- `search_results` — list from `search_listings` (step 2)
- `selected_item` — `search_results[0]`, the dict fed into `suggest_outfit` (step 2B)
- `wardrobe` — the wardrobe dict passed in
- `outfit_suggestion` — string from `suggest_outfit`, fed into `create_fit_card` (step 3)
- `fit_card` — string from `create_fit_card` (step 4)
- `error` — set only on early termination; `None` otherwise

The key flows the rubric cares about: `search_results[0]` → `selected_item` →
`suggest_outfit` input → its output → `create_fit_card` input. The user never
re-enters the item; it lives in the session and is passed by reference. `app.py`
reads the finished session and maps `selected_item`, `outfit_suggestion`,
`fit_card` (or `error`) to the three UI panels.

---

## Error Handling

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| search_listings | No listings match the query | Returns `[]`; the loop writes `session["error"]` = "No listings matched '<query>'. Try raising your max price, dropping the size filter, or using broader keywords." and returns early — `suggest_outfit` is never called with empty input. |
| suggest_outfit | Wardrobe is empty (new user) | Not treated as an error — routes to a general-advice prompt and returns styling ideas for the item's category/style. If the LLM call itself errors, returns a generic fallback styling string. |
| create_fit_card | Outfit input missing/incomplete | If `outfit` is empty/whitespace, returns "⚠️ Can't write a fit card without an outfit suggestion — the styling step didn't produce one." instead of crashing. LLM errors fall back to a simple caption built from the item fields. |

---

## Architecture

```
User query ("vintage graphic tee under $30, size M")
    │
    ▼
run_agent()  ── Planning Loop ───────────────────────────────────────────┐
    │                                                                     │
    │  [1] parse query (regex) → session["parsed"] = {desc, size, price}  │
    │                                                                     │
    ├─►[2] search_listings(description, size, max_price)                  │
    │        │ results == []                                              │
    │        ├──► session["error"] = "No listings matched… loosen X" ─────┤
    │        │                                          (return early) ───┤
    │        │ results == [item, ...]                                     │
    │        ▼                                                            │
    │   session["search_results"] = results                              │
    │   session["selected_item"]  = results[0]                           │
    │        │                                                            │
    ├─►[3] suggest_outfit(selected_item, wardrobe)                        │
    │        │   (empty wardrobe → general advice; LLM error → fallback)  │
    │        ▼                                                            │
    │   session["outfit_suggestion"] = "..."                             │
    │        │                                                            │
    └─►[4] create_fit_card(outfit_suggestion, selected_item)             │
             │  (empty outfit → error string; LLM error → fallback)       │
             ▼                                                            │
        session["fit_card"] = "..."                                      │
             │                                          ◄─ error path ────┘
             ▼                                             returns here
        return session  ──►  app.py maps session → 3 UI panels
                              (listing / outfit / fit card, or error)
```

---

## AI Tool Plan

**Milestone 3 — Individual tool implementations:**
I'll use **Claude (Claude Code)**. For each tool I paste that tool's block above
(what it does, inputs with types, return value, failure mode) and ask Claude to
implement just that function in `tools.py`.
- `search_listings`: give the Tool 1 block and require it to (a) use
  `load_listings()` from `utils/data_loader.py` rather than re-reading the file,
  (b) filter on all three params, (c) score by keyword overlap and drop zero-score
  items, (d) return `[]` (not raise) on no match. Verify by running 3 queries:
  the example tee query (>0 results), an impossible query (`[]`), and a
  price-capped query (assert every result `<= max_price`).
- `suggest_outfit` / `create_fit_card`: give the Tool 2/3 blocks and require Groq
  `llama-3.3-70b-versatile`, the empty-wardrobe / empty-outfit guards, and a
  try/except around the LLM call. Verify each returns a non-empty `str` and never
  raises, and that `create_fit_card` varies across repeated calls (temperature).

**Milestone 4 — Planning loop and state management:**
I'll give Claude the **Architecture diagram** plus the **Planning Loop** and
**State Management** sections, and ask it to implement `run_agent()` in `agent.py`.
Before trusting the output I check: does it branch on the empty-search result and
return early (not call `suggest_outfit` on `[]`)? Does every value get written into
the `session` dict rather than passed ad hoc? Does it avoid calling all three tools
unconditionally? I verify by running the happy path (all fields populated, `error`
is `None`) and the no-results path (`error` set, `fit_card is None`).

---

## A Complete Interaction (Step by Step)

**Example user query:** "I'm looking for a vintage graphic tee under $30. I mostly
wear baggy jeans and chunky sneakers. What's out there and how would I style it?"

**Step 1 — parse.** Regex pulls `max_price = 30.0` from "under $30"; no explicit
`size` token, so `size = None`; `description` = the cleaned query keywords. Stored
in `session["parsed"]`.

**Step 2 — search_listings.** `search_listings("vintage graphic tee ...", None,
30.0)` scores the dataset; the Y2K Butterfly Baby Tee (`lst_002`, $18, depop,
tags y2k/vintage/graphic tee) scores highest and lands first. Results stored;
`selected_item = results[0]`. Because results is non-empty, the loop continues
(an empty result here would instead set `session["error"]` and return).

**Step 3 — suggest_outfit.** `suggest_outfit(selected_item, example_wardrobe)`
sees the baggy jeans, chunky sneakers, and denim jacket and returns something like
"Tuck the baby tee into your baggy straight-leg jeans, throw the vintage denim
jacket over it, and finish with the chunky white sneakers for an easy Y2K look."
Stored in `session["outfit_suggestion"]`.

**Step 4 — create_fit_card.** `create_fit_card(outfit_suggestion, selected_item)`
returns a caption like "found this y2k butterfly baby tee on depop for $18 and it
was made for my baggy jeans 🦋 denim jacket + chunky sneakers and i'm OUT." Stored
in `session["fit_card"]`.

**Final output to user:** the three panels — the chosen listing (title, price,
platform, condition), the outfit suggestion, and the shareable fit card.
