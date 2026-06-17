# FitFindr

A thrift fashion recommendation agent. Give it a natural language query — what you're looking for, your size, your budget — and it finds secondhand listings, suggests how to style the top result with your wardrobe, and optionally writes a shareable outfit caption.

Built with Groq (`llama-3.3-70b-versatile`) and Gradio.

---

## Setup

```bash
pip install -r requirements.txt
```

Create a `.env` file in the project root:
```
GROQ_API_KEY=your_key_here
```

Get a free key at [console.groq.com](https://console.groq.com).

Run the app:
```bash
python app.py
```

Then open the localhost URL shown in your terminal (usually `http://localhost:7860`).

---

## Tool Inventory

### `search_listings(description, size, max_price)`

**Purpose:** Finds secondhand listings that match the user's query. This is the only tool that does not call the LLM — it uses keyword scoring against the local dataset.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `description` | `str` | Yes | Keywords describing the item (e.g. `"vintage graphic tee"`) |
| `size` | `str` | No | Case-insensitive substring match against listing size (e.g. `"M"` matches `"S/M"`) |
| `max_price` | `float` | No | Maximum price inclusive; listings above this are filtered out before scoring |

**Returns:** `list[dict]` — matching listings sorted by relevance score descending. Each dict contains `id`, `title`, `description`, `category`, `style_tags`, `size`, `condition`, `price`, `colors`, `brand`, `platform`. Returns an empty list if nothing matches — never raises.

**Scoring:** For each listing, a searchable string is built from its title, description, category, brand, style tags, and colors. Score = `sum(searchable.count(kw) for kw in keywords)` — total keyword occurrences, not binary match. This means a listing with "graphic" in both the title and description ranks higher than one with it only in the title.

---

### `suggest_outfit(new_item, wardrobe)`

**Purpose:** Uses the Groq LLM to suggest 1–2 outfit combinations for a thrifted item. Adjusts the prompt based on whether the user has a wardrobe.

| Parameter | Type | Description |
|-----------|------|-------------|
| `new_item` | `dict` | The listing dict for the item being considered |
| `wardrobe` | `dict` | User's wardrobe with an `items` key; may be empty |

**Returns:** `str` — a non-empty outfit suggestion string, prefixed with either `👗 Styled with your wardrobe:` or `✨ General styling advice:` so the user can tell at a glance which path was taken. Returns `""` on LLM failure.

**Wardrobe vs. no wardrobe:** When `wardrobe["items"]` is non-empty, the prompt names specific owned pieces and asks for combinations using them. When empty, the prompt shifts to general styling — what pairs well with this item, what vibe it suits. Both paths produce a useful response; empty wardrobe is not an error.

---

### `create_fit_card(outfit, new_item)`

**Purpose:** Uses the Groq LLM to write a 2–4 sentence shareable caption (Instagram/TikTok style) for the outfit. Called only when the user signals sharing intent.

| Parameter | Type | Description |
|-----------|------|-------------|
| `outfit` | `str` | The outfit suggestion string from `suggest_outfit` |
| `new_item` | `dict` | The listing dict — used to pull item name, price, and platform into the caption |

**Returns:** `str` — a caption that naturally mentions the item name, price, and platform once each. Returns a descriptive error string (not an exception) if `outfit` is empty. Returns `""` on LLM failure.

---

## Planning Loop

The loop in `run_agent()` is what makes this an agent rather than a pipeline. Here is exactly what happens on each iteration:

```
run_agent(query, wardrobe)
│
├── initialize session, append user message to messages
│
└── for each iteration (max 6):
    ├── call LLM with full message history + all 3 tool definitions
    │   (temperature=0 for deterministic tool selection)
    │
    ├── LLM returns plain text → done, return session
    │
    └── LLM returns a tool call:
        ├── parse arguments (or default to {} if null/malformed)
        ├── dispatch tool → reads session for complex inputs (item, wardrobe, outfit)
        ├── append tool result to messages
        ├── if tool set session["error"] → return session immediately
        └── loop back → LLM sees the tool result and decides what to do next
```

**Why the LLM drives the loop, not the code:** The LLM sees the full message history on every call, including all previous tool results. This means it decides at each step whether to call another tool, ask a clarifying question, or return a final answer — based on what it now knows. The code only dispatches what the LLM asks for.

**All three tools are conditional.** The LLM calls `search_listings` when the user wants to find items, `suggest_outfit` only if they ask for styling advice, and `create_fit_card` only if they express sharing intent. A query like `"any vintage jackets under $50?"` triggers only `search_listings` and returns. A query like `"find me a graphic tee and help me style it"` triggers `search_listings` then `suggest_outfit`, then stops — no fit card unless the user asks to post it.

**Tool ordering is enforced two ways:**
1. The system prompt instructs the LLM: `search_listings` must run before `suggest_outfit`, which must run before `create_fit_card`.
2. The dispatcher enforces it with session guards — `suggest_outfit` returns an error string immediately if `session["selected_item"]` is None; `create_fit_card` returns an error string if `session["outfit_suggestion"]` is empty.

**One call per iteration, not parallel.** Each loop iteration makes exactly one LLM call and dispatches at most one tool. This is deliberate: by feeding the tool result back into the message history before the next LLM call, the model can react to what it learned (e.g., no results found → don't bother suggesting an outfit).

---

## State Management

Every `run_agent()` call creates a fresh session dict — one session per query submission. There is no cross-submission persistence. Within a single call, all state flows through this dict:

| Field | Written by | Read by | Purpose |
|-------|-----------|---------|---------|
| `query` | session init | — | Original query for reference |
| `messages` | every LLM call and tool dispatch | LLM on every call | Full conversation history; gives LLM context across all tool calls within the loop |
| `parsed` | `search_listings` dispatcher | — | Args the LLM extracted from the query |
| `search_results` | `search_listings` dispatcher | `suggest_outfit` error message | Full ranked result list |
| `selected_item` | `search_listings` dispatcher (auto-selects `results[0]`) | `suggest_outfit` dispatcher | The listing dict passed into styling tools |
| `wardrobe` | session init | `suggest_outfit` dispatcher | User's wardrobe, threaded through from `run_agent` |
| `outfit_suggestion` | `suggest_outfit` dispatcher | `create_fit_card` dispatcher | LLM-generated outfit text |
| `fit_card` | `create_fit_card` dispatcher | UI (`handle_query`) | Shareable caption |
| `error` | any dispatcher or `run_agent` | `run_agent` loop, UI | Signals early termination; UI surfaces this in panel 1 |

**Why tools read from session instead of from LLM args for complex inputs:** `suggest_outfit` and `create_fit_card` need the actual item dict and wardrobe dict. If the LLM were required to pass these as arguments, it would hallucinate values — it doesn't have the original dicts in its token context, only a text summary. The dispatcher reads `session["selected_item"]` and `session["wardrobe"]` directly, which are the authoritative values set by prior tool calls.

**Scoped downstream clear on retry:** When `search_listings` runs, it clears `search_results`, `selected_item`, `outfit_suggestion`, `fit_card`, and `error` before calling the function. When `suggest_outfit` runs, it clears `outfit_suggestion`, `fit_card`, and `error`. This ensures a retry never carries over stale results from a previous attempt.

---

## Error Handling
<img width="2357" height="1257" alt="0ef8d25751f565d0199577e652b89f70" src="https://github.com/user-attachments/assets/8e33eddd-a33d-4bca-bff1-8326eeaba13a" />

### `search_listings`

**No results found:**
The dispatcher formats a specific message: `"No listings found for 'designer ballgown', size 'XXS', under $5.00. Try broader keywords, a different size, or a higher price limit."` Sets `session["error"]`, which causes the loop to return the session immediately. The UI shows this in panel 1; panels 2 and 3 are blank.

**Concrete test case:** Query `"designer ballgown size XXS under $5"` — no listing in the dataset has `price <= 5.0` AND matches those keywords. Confirmed empty list returned from `search_listings`, error message surfaced in the UI, no LLM calls made for outfit or fit card.

**`max_price` passed as string:** The LLM occasionally passes `"30"` instead of `30`. The dispatcher wraps the conversion in `try/except (TypeError, ValueError)` and falls back to `None` (no price filter) rather than crashing.

---

### `suggest_outfit`

**LLM failure (empty return):**
`suggest_outfit()` catches all exceptions and returns `""` on any LLM error. The dispatcher detects the falsy return, sets `session["error"]` to: `"Outfit suggestion unavailable right now — the styling service didn't respond. Here are the listings we found: [titles]."` The session still contains `search_results` and `selected_item`, so the UI shows the listing in panel 1.

**`new_item` not in session:**
If `search_listings` was skipped or failed, `session["selected_item"]` is `None`. The dispatcher returns immediately with: `"Cannot suggest outfit — no item selected. Run search_listings first."` and sets `session["error"]`.

**`new_item` arg is wrong type:**
The LLM sometimes passes a partial dict or a string. The dispatcher validates with `isinstance(args.get("new_item"), dict)` before using it, and falls back to `session["selected_item"]` if the arg fails the check.

---

### `create_fit_card`

**Empty outfit string (guard in tool function):**
`create_fit_card()` checks `if not outfit or not outfit.strip()` before calling the LLM and returns: `"Cannot create a fit card — no outfit suggestion is available. Ask for an outfit suggestion first."` This is the only tool that returns an error string directly from inside the function rather than from the dispatcher.

**LLM failure (empty return):**
Same pattern as `suggest_outfit` — caught in the dispatcher, sets `session["error"]` to: `"Fit card generation unavailable right now — the styling service didn't respond. Your outfit suggestion is still available above."` The session still has `outfit_suggestion`, so panel 2 shows the outfit even when panel 3 is blank.

**Concrete test case:** Calling `create_fit_card(outfit="", new_item={...})` in the test suite directly — confirmed it returns the error string without hitting the LLM at all (no API call made).

---

## Spec Reflection

**What changed from planning.md:**

The biggest design change was the planning loop architecture. The original spec described a single LLM call that returns a `tool_call_list` with all needed tools upfront. During implementation, `llama-3.3-70b-versatile` consistently failed to generate multiple tool calls in a single response when the tool schemas included complex object-type parameters — it would revert to a text-based `<function=name{...}>` format that Groq rejects.

The fix was a proper planning loop: each iteration makes one LLM call with `temperature=0`, dispatches the single tool returned, feeds the result back into message history, and then calls the LLM again. This is architecturally more correct for an agentic loop — the model can react to tool results before deciding the next step — but it means 2–3 LLM calls per full query instead of 1.

A related change: `suggest_outfit` and `create_fit_card` originally had their full parameter schemas with `"type": "object"` entries for `new_item` and `wardrobe`. Without `"properties"` defined on those objects, the model hallucinated values and generated malformed JSON. The fix was to mark those parameters as optional (`"required": []`) with `"additionalProperties": true`, and have the dispatcher always read the authoritative values from session state.

**What the spec got right:**

The state management design held up exactly as planned. The session dict as a single source of truth, scoped downstream-clearing on retry, and the dispatcher reading complex inputs from session rather than from LLM args — all of these worked as designed and prevented the hallucination problems that would have occurred if the LLM had been asked to pass full listing dicts as tool arguments.

The wardrobe-empty path for `suggest_outfit` also worked cleanly as designed — it never raises, always returns a useful string, and the label prefix (`👗` vs `✨`) was added during implementation to make the distinction visible to users.

---

## AI Usage

### Instance 1: Implementing `search_listings` and its test suite

**Input given to Claude Code:**
- The Tool 1 spec section from `planning.md` (description of keyword scoring, size substring match, price filter, empty-result behavior)
- The inline docstring and TODO comments in `tools.py`
- The structure of a listing dict from `data/listings.json`

**What it produced:**
A complete `search_listings()` implementation using `sum(searchable.count(kw) for kw in keywords)` for scoring, plus 20 pytest cases covering price filter, size filter, multi-keyword ranking, and empty results.

**What I changed:**
The initial scoring used `sum(1 for kw in keywords if kw in searchable)` — binary match, one point per keyword. This ranked "graphic tee" results incorrectly because two different listings both matched two keywords. I identified the specific failing case (lst_006 vs lst_002 for "vintage graphic tee") and directed Claude to fix the scoring to count total occurrences instead. The fix was a one-line change but required understanding the ranking behavior, which Claude didn't catch from the spec alone.

---

### Instance 2: Implementing `run_agent()` and `_dispatch_tool()`

**Input given to Claude Code:**
- The full Planning Loop section from `planning.md` (LLM-driven loop, conditional dispatch, scoped downstream-clear on retry)
- The Architecture ASCII diagram from `planning.md`
- The Error Handling table
- The step-by-step TODO comments inside `run_agent()` in `agent.py`

**What it produced:**
A working `run_agent()` with session initialization, single LLM call, tool dispatch loop, and message history management. The `_dispatch_tool()` function with scoped downstream-clearing for each tool.

**What I changed:**
The initial implementation had `suggest_outfit` and `create_fit_card` tool definitions with `"properties": {}` and `"required": []` (empty schemas). I had asked for this because the dispatcher reads those inputs from session, so the LLM shouldn't need to provide them. The problem was that empty schemas caused `llama-3.3-70b-versatile` to generate tool calls in the wrong format. I directed Claude to add proper property definitions with `"additionalProperties": true` and remove those params from `required`, which fixed the format issue while keeping the dispatcher reading from session. This required understanding how Groq's tool calling works under the hood — the spec didn't anticipate this constraint.

The planning loop itself also changed from single-call to multi-call (see Spec Reflection above). I identified the root cause (LLM sampling non-determinism with `temperature` defaulting to ~1.0) and directed Claude to add `temperature=0` to the planning LLM calls to make tool format selection deterministic.
