# AGENTS.md — agents/

This directory contains the two LLM agent modules. Both follow the same generator protocol and use the Groq client passed in from `app.py`.

---

## Generator Protocol (Required)

Every `run()` function in this directory MUST be a Python generator that yields exactly two kinds of tuples:

```python
yield ("status", "<short human-readable string>")  # any number of these
yield ("done",   "<final markdown response>")       # exactly once, last yield
```

`handle_response()` in `app.py` drives the iteration. Breaking this contract will break the UI.

---

## meal_agent.py

**Purpose:** Given the user's ingredients and conversation history, produce a structured weekly batch-cook meal plan.

**Signature:**
```python
def run(client: groq.Groq, history: list, system_prompt: str, user_message: str) -> Generator
```

**Inputs:**
- `client` — authenticated `groq.Groq` instance (from `app.py`)
- `history` — all prior messages in `meal_messages` **excluding** the current user turn (app.py slices with `[:-1]`)
- `system_prompt` — `MEAL_PLAN_PROMPT` from `config.py`, already formatted with `{location}`
- `user_message` — the current user turn, possibly prefixed with Apple Notes context

**What it does:**
- Calls Groq chat completions with `TEXT_MODEL` (`llama-3.1-8b-instant`)
- Optionally yields intermediate status messages during any multi-step reasoning
- Yields `("done", final_markdown)` with the complete meal plan

**Constraints:**
- Must handle `content` that is either a plain string or a list of content blocks (for image messages)
- Do not load prompts from inside this file — receive `system_prompt` as a parameter
- Do not call `st.*` — this module must be UI-agnostic

---

## shopping_agent.py

**Purpose:** Look up real grocery prices near the user's location and synthesize a price comparison table.

**Signature:**
```python
def run(client: groq.Groq, location: str, user_message: str, kroger_id: str, kroger_secret: str) -> Generator
```

**Inputs:**
- `client` — authenticated `groq.Groq` instance
- `location` — full address string from sidebar (guaranteed non-empty by `location_guard()`)
- `user_message` — ingredient list, possibly prefixed with Apple Notes context
- `kroger_id` / `kroger_secret` — Kroger OAuth credentials (may be empty strings if not configured)

**What it does:**
1. Yields `("status", "Finding nearby stores...")` (or similar)
2. Calls the Kroger API to find nearby store locations
3. Yields `("status", "Looking up prices...")` (or similar)
4. Calls Kroger product search for each ingredient
5. Formats results using `SHOPPING_SYNTHESIS_PROMPT` from `config.py`
6. Calls Groq to synthesize a markdown price table
7. Yields `("done", final_markdown)`

**Constraints:**
- If `kroger_id` or `kroger_secret` is empty, gracefully degrade: yield a `("done", ...)` message explaining that Kroger credentials are not configured
- Do not invent prices — only pass real API data to the LLM prompt
- Do not call `st.*` — UI-agnostic
- All prompt templates live in `config.py` — import from there, don't inline them

---

## Adding a New Agent

1. Create `agents/new_agent.py`
2. Implement `def run(...) -> Generator` following the protocol above
3. Import and wire it into `app.py` in a new tab or as a fallback
4. Add a section to this file documenting the new agent's signature and behavior
