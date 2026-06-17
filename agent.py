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

import json
import os

from dotenv import load_dotenv
from groq import Groq

from tools import search_listings, suggest_outfit, create_fit_card

load_dotenv()

_MODEL = "llama-3.3-70b-versatile"

_SYSTEM_PROMPT = """You are FitFindr, a friendly fashion assistant specializing in thrifted and secondhand clothing.

You have three tools available. Use them only when the user's request warrants it:

- search_listings: call when the user wants to find or browse items. Extract description, size, and max_price from their message.
- suggest_outfit: call only when the user asks for styling or outfit advice AND search_listings has already run.
- create_fit_card: call only when the user wants to share or post their outfit (e.g. "make a caption", "share this fit"). Requires suggest_outfit to have run first.

Tool ordering rules (strict — never skip steps):
1. search_listings must run before suggest_outfit.
2. suggest_outfit must run before create_fit_card.

If search returns no results, tell the user clearly and ask them to try different keywords, size, or price.
If a tool fails, report the error and stop — do not attempt to fill in missing results.
Be conversational, warm, and specific in your responses."""

_TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_listings",
            "description": "Search thrifted clothing listings by keyword description, optional size, and optional max price. Returns ranked matching items.",
            "parameters": {
                "type": "object",
                "properties": {
                    "description": {
                        "type": "string",
                        "description": "Keywords describing what the user is looking for (e.g. 'vintage graphic tee')"
                    },
                    "size": {
                        "type": "string",
                        "description": "Size filter — case-insensitive substring match (e.g. 'M', 'XL'). Omit if not specified."
                    },
                    "max_price": {
                        "type": "number",
                        "description": "Maximum price in dollars, inclusive. Omit if not specified."
                    }
                },
                "required": ["description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "suggest_outfit",
            "description": "Suggest 1-2 outfit combinations for a thrifted item using the user's existing wardrobe. If the wardrobe is empty, returns general styling advice instead. Call only after search_listings has run.",
            "parameters": {
                "type": "object",
                "properties": {
                    "new_item": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": "The listing dict for the thrifted item to style. Provided by the session — can be omitted."
                    },
                    "wardrobe": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": "The user's wardrobe dict. Provided by the session — can be omitted."
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_fit_card",
            "description": "Generate a 2-4 sentence shareable Instagram/TikTok caption for the outfit. Casual and authentic tone. Call only after suggest_outfit has run.",
            "parameters": {
                "type": "object",
                "properties": {
                    "outfit": {
                        "type": "string",
                        "description": "The outfit suggestion string from suggest_outfit. Provided by the session — can be omitted."
                    },
                    "new_item": {
                        "type": "object",
                        "additionalProperties": True,
                        "description": "The listing dict for the thrifted item. Provided by the session — can be omitted."
                    }
                },
                "required": []
            }
        }
    }
]


# ── Groq client ───────────────────────────────────────────────────────────────

def _get_groq_client() -> Groq:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY not set. Add it to a .env file in the project root.")
    return Groq(api_key=api_key)


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one run_agent call.

    The session dict is the single source of truth for everything that happens
    during a run — it stores the original query, parsed parameters, tool results,
    and any error that caused early termination.

    You may add fields to this dict as needed for your implementation.
    """
    return {
        "query": query,              # original user query
        "messages": [],              # full conversation history for LLM context
        "parsed": {},                # extracted description / size / max_price
        "search_results": [],        # list of matching listing dicts
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "error": None,               # set if the interaction ended early
    }


# ── tool dispatcher ───────────────────────────────────────────────────────────

def _dispatch_tool(name: str, args: dict, session: dict) -> str:
    """
    Execute a named tool, update session state, and return the result as a
    string to be appended to the conversation as a tool message.

    Clears downstream session fields before each dispatch so a retry always
    starts from a clean slate for that tool and everything after it.
    """
    if name == "search_listings":
        session.update({
            "parsed": args,
            "search_results": [],
            "selected_item": None,
            "outfit_suggestion": None,
            "fit_card": None,
            "error": None,
        })

        description = args.get("description") or ""
        if not description.strip():
            session["error"] = "Cannot search — no description provided."
            return session["error"]

        size = args.get("size") or None
        raw_price = args.get("max_price")
        try:
            max_price = float(raw_price) if raw_price is not None else None
        except (TypeError, ValueError):
            max_price = None

        results = search_listings(description=description, size=size, max_price=max_price)
        session["search_results"] = results

        if not results:
            size_part = f", size '{size}'" if size else ""
            price_part = f", under ${max_price:.2f}" if max_price is not None else ""
            session["error"] = (
                f"No listings found for '{description}'{size_part}{price_part}. "
                f"Try broader keywords, a different size, or a higher price limit."
            )
            return session["error"]

        session["selected_item"] = results[0]

        def _fmt(r):
            title = r.get("title") or "Unknown"
            price = r.get("price")
            price_str = f"${float(price):.2f}" if price is not None else "N/A"
            platform = r.get("platform") or "unknown"
            sz = r.get("size") or "?"
            return f"{title} — {price_str} on {platform} (size {sz})"

        lines = "\n".join(f"{i + 1}. {_fmt(r)}" for i, r in enumerate(results))
        top_title = results[0].get("title") or "Unknown"
        print(f"[search_listings] dispatched — {len(results)} result(s), selected: {top_title}")
        return (
            f"Found {len(results)} item(s). Auto-selected the top result.\n\n"
            f"{lines}\n\nSelected: {top_title}"
        )

    elif name == "suggest_outfit":
        session.update({"outfit_suggestion": None, "fit_card": None, "error": None})

        new_item = args.get("new_item") if isinstance(args.get("new_item"), dict) else None
        new_item = new_item or session.get("selected_item")
        wardrobe = args.get("wardrobe") if isinstance(args.get("wardrobe"), dict) else None
        wardrobe = wardrobe or session.get("wardrobe") or {"items": []}

        if not new_item or not isinstance(new_item, dict):
            session["error"] = "Cannot suggest outfit — no item selected. Run search_listings first."
            return session["error"]

        result = suggest_outfit(new_item=new_item, wardrobe=wardrobe)

        if not result:
            titles = [r["title"] for r in session["search_results"]]
            session["error"] = (
                f"Outfit suggestion unavailable right now — the styling service didn't respond. "
                f"Here are the listings we found: {', '.join(titles)}."
            )
            return session["error"]

        session["outfit_suggestion"] = result
        print(f"[suggest_outfit] dispatched — {len(result)} chars")
        return result

    elif name == "create_fit_card":
        session.update({"fit_card": None, "error": None})

        outfit = (args.get("outfit") if isinstance(args.get("outfit"), str) else None) or session.get("outfit_suggestion") or ""
        new_item = (args.get("new_item") if isinstance(args.get("new_item"), dict) else None) or session.get("selected_item") or {}

        if not outfit:
            session["error"] = "Cannot create fit card — no outfit suggestion available. Run suggest_outfit first."
            return session["error"]

        result = create_fit_card(outfit=outfit, new_item=new_item)

        if not result:
            session["error"] = (
                "Fit card generation unavailable right now — the styling service didn't respond. "
                "Your outfit suggestion is still available above."
            )
            return session["error"]

        session["fit_card"] = result
        print(f"[create_fit_card] dispatched — {len(result)} chars")
        return result

    else:
        print(f"[WARNING] Unknown tool requested: {name}")
        return f"Unknown tool: {name}"


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
        output fields (outfit_suggestion, fit_card) may be None.

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

    Flow:
        1. Initialize session and append user message to session["messages"].
        2. Enter the planning loop — each iteration makes one LLM call with
           parallel_tool_calls=False so the model returns at most one tool per turn.
        3. If the LLM returns a tool call, dispatch it and feed the result back
           into messages, then loop.
        4. If the LLM returns no tool calls (plain text), the loop ends.
        5. Return the completed session.
    """
    session = _new_session(query, wardrobe)
    session["messages"].append({"role": "user", "content": query})

    print(f"[run_agent] started — query={query!r}")

    try:
        client = _get_groq_client()
    except ValueError as e:
        print(f"[ERROR] Groq client init failed: {e}")
        session["error"] = str(e)
        return session

    dispatched = []
    MAX_ITERATIONS = 6

    for iteration in range(MAX_ITERATIONS):
        print(f"[run_agent] LLM call #{iteration + 1}")

        response = None
        for attempt in range(3):
            try:
                response = client.chat.completions.create(
                    model=_MODEL,
                    messages=[{"role": "system", "content": _SYSTEM_PROMPT}] + session["messages"],
                    tools=_TOOL_DEFINITIONS,
                    tool_choice="auto",
                    temperature=0,
                )
                break
            except Exception as e:
                if "tool_use_failed" in str(e) and attempt < 2:
                    print(f"[WARNING] tool_use_failed on attempt {attempt + 1}, retrying...")
                    continue
                print(f"[ERROR] LLM call failed — {type(e).__name__}: {e}")
                session["error"] = f"Agent unavailable — LLM call failed: {e}"
                return session

        if not response or not response.choices:
            session["error"] = "Agent unavailable — LLM returned an empty response."
            return session

        msg = response.choices[0].message
        print(f"[LLM response raw] {response}")
        print(f"[LLM response] content={msg.content!r}  tool_calls={[tc.function.name for tc in msg.tool_calls] if msg.tool_calls else None}")

        assistant_entry = {"role": "assistant", "content": msg.content}
        if msg.tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id or "",
                    "type": "function",
                    "function": {
                        "name": tc.function.name or "",
                        "arguments": tc.function.arguments or "{}",
                    },
                }
                for tc in msg.tool_calls
            ]
        session["messages"].append(assistant_entry)

        if not msg.tool_calls:
            print(f"[run_agent] complete — no more tool calls, dispatched: {dispatched}")
            return session

        tc = msg.tool_calls[0]
        name = tc.function.name or ""

        if not name:
            session["error"] = "Agent error — LLM returned a tool call with no function name."
            return session

        try:
            args = json.loads(tc.function.arguments or "{}") or {}
        except (json.JSONDecodeError, TypeError) as e:
            print(f"[ERROR] Failed to parse args for tool '{name}': {e}")
            session["error"] = f"Failed to parse arguments for tool '{name}'"
            return session

        if not isinstance(args, dict):
            print(f"[WARNING] Args for '{name}' parsed to non-dict ({type(args).__name__}), defaulting to {{}}")
            args = {}

        print(f"[run_agent] dispatching {name} — args={args}")
        try:
            result = _dispatch_tool(name, args, session)
        except Exception as e:
            print(f"[ERROR] _dispatch_tool '{name}' raised: {type(e).__name__}: {e}")
            session["error"] = f"Tool '{name}' failed unexpectedly: {e}"
            return session
        dispatched.append(name)

        session["messages"].append({
            "role": "tool",
            "tool_call_id": tc.id,
            "content": str(result),
        })

        if session["error"]:
            print(f"[WARNING] Tool '{name}' set error — stopping")
            return session

    print(f"[WARNING] run_agent hit max iterations ({MAX_ITERATIONS})")
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
