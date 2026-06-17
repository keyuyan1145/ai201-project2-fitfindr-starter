# FitFindr — planning.md

> Complete this document before writing any implementation code.
> Your spec and agent diagram are what you'll use to direct AI tools (Claude, Copilot, etc.) to generate your implementation — the more specific they are, the more useful the generated code will be.
> Your planning.md will be reviewed as part of your submission.
> Update it before starting any stretch features.

---

## Tools

List every tool your agent will use. For each tool, fill in all four fields.
You must have at least 3 tools. The three required tools are listed — add any additional tools below them.

### Tool 1: search_listings

**What it does:**
<!-- Describe what this tool does in 1–2 sentences -->

For the user requested item, search in the data listings for list of matching items filtered by size and prie if provided and sorted by most relevance.

**Input parameters:**
<!-- List each parameter, its type, and what it represents -->
- `description` (str): keywords, descriptions for the items, will match against description field on the listing based on number of keyword matches. 
- `size` (str): specify the requested size, will be substring match after sanitizing string and consider case sensitivity.
- `max_price` (float): max price for the item, including the max price itself. This will act like a filter and the result set must contain all items less than or equal to this price.

**What it returns:**
<!-- Describe the return value — what fields does a result contain? -->
Return list of items (raw item listing in the data set) sorted by most relevance. 

**What happens if it fails or returns nothing:**
<!-- What should the agent do if no listings match? -->
No listing match will prompt to user to try something different as previous request did not match with any listing in data set.

---

### Tool 2: suggest_outfit

**What it does:**
<!-- Describe what this tool does in 1–2 sentences -->
This tool takes the new item dictionary that the user is looking to purchase and dictionary containing what's available in user's existing wardrobe to suggest an outfit.

**Input parameters:**
<!-- List each parameter, its type, and what it represents -->
- `new_item` (dict): one of the items returned from search_listings() that the user is looking to purchase. 
- `wardrobe` (dict): dictionary containing list of items in the user's wardrobe that the user would like to form an outlet with the new item looking to purchase. 

**What it returns:**
<!-- Describe the return value -->
String suggestin outfit for the new item looking to purchase that matches with the existing user's wardrobe. If user wardrobe is empty, return string offering general styling advice for the new item.

**What happens if it fails or returns nothing:**
<!-- What should the agent do if the wardrobe is empty or no outfit can be suggested? -->
If wardrobe is empty or no outfit can be suggested, offer general styling advice for the new item looking to purchase.

---

### Tool 3: create_fit_card

**What it does:**
<!-- Describe what this tool does in 1–2 sentences -->
Generate a short, sharable blog content from the suggested outfit and the item user is looking to purchase. 

**Input parameters:**
<!-- List each parameter, its type, and what it represents -->
- `outfit` (str): string description for the suggested outfit returned by suggest_outfit()
- `new_item` (dict): item user looking to purchase and form outfit with, returned from search_listings()

**What it returns:**
<!-- Describe the return value -->
Shareable blog content about the suggested outfit possible from the thrifted item, and includes the following requirements:
- casual and authentic tone
- include itemname, price, and platform naturally
- capture outfit vibe
- different output for different inputs (user higher LLM temperature)

**What happens if it fails or returns nothing:**
<!-- What should the agent do if the outfit data is incomplete? -->
Return a descriptive error message string to user.

---

### Additional Tools (if any)

<!-- Copy the block above for any tools beyond the required three -->

---

## Planning Loop

**How does your agent decide which tool to call next?**

The planning loop is LLM-driven. On every user message turn, `run_agent` appends the user message to `session["messages"]` and calls the LLM with the full message history and all three tools exposed. The LLM response determines the next action:

- **If the LLM returns a `tool_call_list`:** `run_agent` dispatches each call via `dispatch_tool()`, appends the tool results back to `session["messages"]`, then calls the LLM again with the updated history. This repeats until the LLM returns plain text with no further tool calls.
- **If the LLM returns plain text (no tool calls):** `run_agent` returns the text to the user and waits for the next input. The session persists across turns so context is preserved.

**All three tools are conditional** — the LLM only calls a tool if the user's intent warrants it:

- `search_listings` is called when the user asks to find or browse items.
- `suggest_outfit` is called only if the user asks for styling or outfit advice — not every search needs one. It requires `selected_item` to already be set in the session.
- `create_fit_card` is called only if the user signals intent to share or post (e.g., "make a caption", "share this fit"). It requires `outfit_suggestion` to already be in the session.

**Tool ordering is strictly enforced** by the system prompt and input dependencies — the LLM is instructed never to call a later tool without the prior tool's output available in the session:

1. LLM first extracts `description`, `size`, and `max_price` from the user query and saves them to `session["parsed"]` before calling `search_listings`.
2. After `search_listings` returns, the top-ranked result is auto-selected as `session["selected_item"]`. The full result list is returned to the LLM so it can present all options to the user in its response.

   > **Alternative design (not implemented):** Multi-turn item selection — results are presented to the user and `run_agent` returns, waiting for the user to reply with their choice. `session["selected_item"]` is only set on the next call once the user confirms. Requires a persistent session across calls.
3. `suggest_outfit` must complete and its result stored in `session["outfit_suggestion"]` before `create_fit_card` can be called.

On any tool or LLM failure, `run_agent` sets `session["error"]` and returns immediately — the LLM does not continue calling further tools or attempt to fill in missing results.

**Session reset on retry:** When a new user message arrives after a prior error, there is no full session wipe. Instead, `run_agent` clears only the fields downstream of whichever tool the LLM decides to retry, scoped by dependency:

- LLM retries `search_listings` → clears `parsed`, `search_results`, `selected_item`, `outfit_suggestion`, `fit_card`, `error`
- LLM retries `suggest_outfit` → clears `outfit_suggestion`, `fit_card`, `error`
- LLM retries `create_fit_card` → clears `fit_card`, `error`

The LLM already has the full message history including the prior error, so it naturally determines how far back to retry. `session["messages"]` and `session["wardrobe"]` are never cleared — they persist for the entire user session.

---

## State Management

**How does information from one tool get passed to the next?**

All state is stored in the session dict created by `_new_session()` at the start of each `run_agent` call — **one session per call**. Each call to `run_agent` starts fresh. The `messages` field accumulates conversation history within that single call so the LLM has full context for all tool dispatches that happen inside the loop.

> **Alternative design (not implemented):** Session persists across the full user conversation — `_new_session()` is called once at app start and the same dict is passed into every `run_agent` call. This enables multi-turn flows where the user can search in one prompt and ask for outfit advice in a later prompt without losing prior results. Requires changing the `run_agent` signature to accept an existing session.

Tools never call each other directly. `run_agent` reads from the session to build each tool's inputs and writes results back after each `dispatch_tool()` call.

| Field | Set when | Purpose |
|-------|----------|---------|
| `query` | Session init | Original user query for reference |
| `messages` | Every turn | Full conversation history passed to LLM on each call; enables multi-turn context |
| `parsed` | After LLM parses query | Holds `description`, `size`, `max_price` extracted by the LLM — saved before `search_listings` is called |
| `search_results` | After `search_listings` | Full ordered list of matching listings presented to the user for selection |
| `selected_item` | After user confirms their item choice | The listing dict the user picked — explicitly saved to session before `suggest_outfit` is called so the input is deterministic |
| `wardrobe` | Session init | User's wardrobe passed through to `suggest_outfit` |
| `outfit_suggestion` | After `suggest_outfit` | LLM-generated outfit string — required input to `create_fit_card` |
| `fit_card` | After `create_fit_card` | Shareable caption string — only populated if user expressed sharing intent |
| `error` | On any tool or LLM failure | Descriptive error message; signals `run_agent` to return immediately without calling further tools |

---

## Error Handling

For each tool, describe the specific failure mode you're handling and what the agent does in response.

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| `search_listings` | No results match the query | `session["error"]` is set. No further tools are called. Error message: `"No listings found for '[description]'[, size '[size]'][, under $[max_price]]. Try broader keywords, a different size, or a higher price limit."` |
| `suggest_outfit` | Wardrobe is empty | Not an error — the LLM is expected to return general styling advice specific to `selected_item`. Only treated as a failure if the returned string is empty or null. |
| `suggest_outfit` | LLM call fails or returns empty string | `session["error"]` is set. `create_fit_card` is not called. Partial session returned with `search_results` and `selected_item` intact. Error message: `"Outfit suggestion unavailable right now — the styling service didn't respond. Here are the listings we found: [search_results titles]."` |
| `create_fit_card` | Outfit input is missing or incomplete | Tool returns an error string directly without calling the LLM. Error message: `"Cannot create a fit card — no outfit suggestion is available. Ask for an outfit suggestion first."` |
| `create_fit_card` | LLM call fails or returns empty string | `session["error"]` is set. Partial session returned with `selected_item` and `outfit_suggestion` intact. Error message: `"Fit card generation unavailable right now — the styling service didn't respond. Your outfit suggestion is still available above."` |

---

## Architecture

```
  ┌─────────────────────────────────────────────────────────────┐
  │                       Session State                          │
  │  messages · parsed · search_results · selected_item          │
  │  wardrobe · outfit_suggestion · fit_card · error             │
  └──────────────────────────┬──────────────────────────────────┘
                             │ read / write
  ┌──────────┐  user msg    ┌▼─────────────────────────────────┐
  │   User   │ ────────────►│    run_agent  (Planning Loop)    │
  │          │ ◄──────────── └─────────────────┬───────────────┘
  └──────────┘  text/error                     │
                                               │ messages + tools
                                               ▼
                                      ┌─────────────────┐
                                      │   LLM (Groq)    │
                                      └────────┬────────┘
                                               │
                                  ┌────────────┴─────────────┐
                                  │                          │
                            tool_call_list              plain text
                                  │                          │
                                  ▼                    return to User
                           dispatch_tool()
                                  │
             ┌────────────────────┼──────────────────────┐
             │  (conditional)     │  (conditional)        │  (conditional)
             ▼                    ▼                       ▼
  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
  │ search_listings  │  │  suggest_outfit  │  │ create_fit_card  │
  │  filter + rank   │  │    Groq LLM      │  │    Groq LLM      │
  └────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
           │                     │                      │
        ┌──┴──┐               ┌──┴──┐                ┌──┴──┐
        │     │               │     │                │     │
     empty  result          fail  result           fail  result
     list     │               │     │                │     │
       │      │               ▼     │                ▼     │
       ▼      │          [error]    │           [error]    │
  [error]     │          partial    │           partial    │
  return      │          return     │           return     │
  early       │          (item      │           (item +    │
  ───────     │           shown)    │            outfit    │
              ▼                     ▼            shown)    ▼
       session["search_results"] session["outfit_suggestion"] session["fit_card"]
       → append to messages      → append to messages     → append to messages
       → loop back to LLM        → loop back to LLM       → loop back to LLM
```

**Notes:**
- All three tools are **conditional** — only dispatched if the LLM includes them in `tool_call_list` based on user intent.
- `suggest_outfit` requires `selected_item` in session; `create_fit_card` requires `outfit_suggestion`.
- Single search result: auto-set as `selected_item`, fed back to LLM to decide next step without user prompt.
- Session reset on retry: before dispatching a retried tool, clear that tool's output and all downstream fields. `messages` and `wardrobe` are never cleared.

---

## AI Tool Plan

<!-- For each part of the implementation below, describe:
     - Which AI tool you plan to use (Claude, Copilot, ChatGPT, etc.)
     - What you'll give it as input (which sections of this planning.md, your agent diagram)
     - What you expect it to produce
     - How you'll verify the output matches your spec before moving on

     "I'll use AI to help me code" is not a plan.
     "I'll give Claude my Tool 1 spec (inputs, return value, failure mode) and ask it to implement
     search_listings() using load_listings() from the data loader — then test it against 3 queries
     before trusting it" is a plan. -->

**Milestone 3 — Individual tool implementations:**

**Tool: Claude Code**

Each tool will be implemented one at a time in `tools.py`.

- **`search_listings`** — Input: Tool 1 spec (description, size, max_price parameters; return value; failure mode) from the Tools section, plus the inline docstring and comments inside `tools.py`. Claude Code will produce a function that loads listings via `load_listings()`, filters by `max_price` and `size` (case-insensitive substring), scores each listing by keyword overlap against `description`, drops zero-score results, and returns the list sorted by score descending. Verification: run tests with (1) a broad keyword that matches multiple items, (2) a size + price filter that narrows the set, (3) a query that matches nothing — confirm empty list is returned and no exception is raised.

- **`suggest_outfit`** — Input: Tool 2 spec (new_item, wardrobe parameters; return value; empty-wardrobe behavior) from the Tools section, the Error Handling table row for this tool, and the wardrobe schema from `data/wardrobe_schema.json`. Claude Code will produce a function that builds a prompt using `new_item` fields and wardrobe items, calls the Groq LLM, and returns the response string. For empty wardrobe, the prompt asks for general styling advice for the specific item. Verification: run tests with (1) the example wardrobe, (2) an empty wardrobe — confirm a non-empty string is returned in both cases, and that the wardrobe items or the item name appear in the output.

- **`create_fit_card`** — Input: Tool 3 spec (outfit, new_item parameters; tone requirements; failure mode) from the Tools section and the Error Handling table row for this tool. Claude Code will produce a function that guards against empty outfit string, builds a prompt with item name/price/platform and the outfit description, calls the Groq LLM at higher temperature, and returns the caption string. Verification: run tests with (1) a valid outfit string and item dict — confirm item name, price, and platform appear in the output; (2) an empty outfit string — confirm a descriptive error string is returned rather than an exception.

---

**Milestone 4 — Planning loop and state management:**

**Tool: Claude Code**

Input: the full Planning Loop section, State Management section, Architecture diagram, Error Handling table, and the inline docstring and step-by-step comments inside `run_agent()` in `agent.py`. Claude Code will be asked to implement `run_agent()` and `dispatch_tool()` to match the design exactly — LLM-driven loop, conditional tool dispatch, session persisting across turns, scoped downstream-clear on retry, and early/partial returns on error.

Expected output: a working `run_agent()` that (1) appends messages and calls the LLM each turn, (2) dispatches only the tools the LLM requests, (3) enforces tool ordering via the system prompt, (4) auto-selects `selected_item` when `search_listings` returns exactly one result, (5) clears downstream session fields before retrying a tool, and (6) returns the session with partial results and `error` set on any failure.

Verification: run tests covering (1) full happy path — search → user selects item → outfit → fit card, (2) search returns no results — confirm error is set and no further tools are called, (3) single search result — confirm item is auto-selected and LLM is called again without user input, (4) `suggest_outfit` failure — confirm session contains `search_results` and `selected_item` but `outfit_suggestion` is None and `error` is set, (5) multi-turn retry after error — confirm downstream fields are cleared and fresh results are stored, (6) user asks only to search with no outfit intent — confirm `suggest_outfit` is never called.

---

## A Complete Interaction (Step by Step)

Write out what a full user interaction looks like from start to finish — tool call by tool call. Use a specific example query.

**Example user query:** "I'm looking for a vintage graphic tee under $30. I mostly wear baggy jeans and chunky sneakers. What's out there and how would I style it?"

**Step 1:**
<!-- What does the agent do first? Which tool is called? With what input? -->
Agent request for search_listings("vintage graphic tee", size="M", max_price=30.0). If tool call success but empty result set, then prompt user to try something different since requested item (or typo) doesn't match any in the sample listings. If tool call failed, then report error back to user.

**Step 2:**
<!-- What happens next? What was returned from step 1? What tool is called now? -->
Take the item id returned from step 1 and call suggest_outfit(new_item=<band_tee_id_from_search_listings>, wardrobe=<user's_wardrobe_schema_with_baggy_jean_and_chunky_sneakers>). 
Since suggestion will be fueled by llm using different promptd, empty response or failure suggests issues on llm side and should report error to user that the tool is unable to generate suggestion at the time, but here are the available thrift items that you have requested.

**Step 3:**
<!-- Continue until the full interaction is complete -->
The outfit suggestion from step 2 would be sufficient is the user is only looking for suggestion and not planning on sharing or posting the outfit anywhere. 
Otherwise, if the user would like to share the outlet, then agent calls create_fit_card(outfit=<step_2_suggestion>, new_item=<band_tee_from_step_1>). Same error handling as step 2 as this tool will be mostly a llm call with a prompt.

**Final output to user:**
<!-- What does the user actually see at the end? -->
Either the raw outfit suggestion from step 2 for more personal use or the outfit card string from step 3 that can be shared. 
