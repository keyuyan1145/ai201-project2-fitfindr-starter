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
2. After `search_listings` returns:
   - **Multiple results:** present the list to the user and wait for them to select an item. `session["selected_item"]` is saved once the user confirms their choice.
   - **Single result:** auto-select it as `session["selected_item"]` without prompting the user. Feed the item directly back to the LLM (not as a user-facing message) and let the LLM decide whether to call `suggest_outfit` immediately (if the original prompt included styling intent) or surface the result to the user first.
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

All state is stored in the session dict created by `_new_session()` at the start of a **user session** — once per conversation, not once per `run_agent` call. The same session persists across multiple user prompts so that a user can search in one message and ask for outfit advice in a later message without losing prior context. `run_agent` receives the existing session and updates it in place each turn.

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
| `search_listings` | No results match the query | LLM receives the empty list, returns a message asking the user to try different keywords, size, or price. `session["error"]` is set. No further tools are called. |
| `suggest_outfit` | Wardrobe is empty | Not an error — the LLM is expected to return general styling advice specific to `selected_item`. Only treated as a failure if the returned string is empty or null. |
| `suggest_outfit` | LLM call fails or returns empty string | `session["error"]` is set. Partial session is returned with `search_results` and `selected_item` intact so the user can still see the listings found. `create_fit_card` is not called. |
| `create_fit_card` | Outfit input is missing or incomplete | Tool returns a descriptive error string directly; LLM surfaces it to the user as the final response. |
| `create_fit_card` | LLM call fails or returns empty string | `session["error"]` is set. Partial session is returned with `selected_item` and `outfit_suggestion` intact so the user still has the outfit recommendation. |

---

## Architecture

<!-- Draw a diagram of your agent showing how the components connect:
     User input → Planning Loop → Tools (search_listings, suggest_outfit, create_fit_card)
                                                                          ↕
                                                                   State / Session
     Show what triggers each tool, how state flows between them, and where error paths branch off.
     ASCII art, a Mermaid diagram (https://mermaid.js.org/syntax/flowchart.html), or an embedded
     sketch are all fine. You'll share this diagram with an AI tool when asking it to implement
     the planning loop and each individual tool. -->

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

**Milestone 4 — Planning loop and state management:**

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
