# AGENTS.md — Maple (Root)

## Project Overview

Maple is a Python/Streamlit web app with two AI-powered features:
1. **Meal Planner** — user lists ingredients (or uploads a photo), gets a week of batch-cook meal plans
2. **Price Finder** — user lists ingredients, gets real nearby store prices via the Kroger API

The app is entirely in Python. There is no frontend build step. Run it with `streamlit run app.py`.

---

## Repository Layout

```
Maple/
├── AGENTS.md               ← you are here
├── app.py                  ← Streamlit UI, routing, session state, autosave
├── config.py               ← All prompt templates + model name constants
├── requirements.txt        ← streamlit, groq, requests
├── chat_history.json       ← flat-file session persistence (do NOT delete; safe to modify)
├── agents/
│   ├── AGENTS.md           ← agent-specific guidance
│   ├── meal_agent.py       ← meal planning LLM agent (generator)
│   └── shopping_agent.py   ← price lookup + LLM synthesis agent (generator)
└── services/
    ├── AGENTS.md           ← service-specific guidance
    ├── apple_notes.py      ← macOS Apple Notes integration
    └── vision.py           ← image encoding + Groq vision ingredient extraction
```

---

## Environment & Secrets

All secrets are read from Streamlit secrets (`st.secrets`), NOT environment variables. When running locally this means `.streamlit/secrets.toml`. Do not hardcode keys.

| Secret key             | Purpose                          |
|------------------------|----------------------------------|
| `GROQ_API_KEY`         | All Groq LLM + vision calls      |
| `KROGER_CLIENT_ID`     | Kroger API OAuth client ID       |
| `KROGER_CLIENT_SECRET` | Kroger API OAuth client secret   |
| `DATABASE_URL`         | Optional: Postgres/SQLite URL for multi-user chat persistence |

---

## Models (defined in `config.py`)

| Constant        | Value                          | Used for                        |
|-----------------|--------------------------------|---------------------------------|
| `VISION_MODEL`  | `llama-3.2-11b-vision-preview` | Image ingredient analysis       |
| `TEXT_MODEL`    | `llama-3.1-8b-instant`         | Meal planning + title gen       |

When changing models, update `config.py` only — do not hardcode model strings anywhere else.

---

## Core Patterns

### Agent Generator Protocol
Both agents expose a `run()` function that is a **Python generator**. Callers must iterate it. Each `yield` is a tuple:

```python
yield ("status", "Human-readable status string")  # shown in st.status()
yield ("done", "Final markdown response string")   # consumed as the assistant reply
```

`handle_response()` in `app.py` is the only consumer. Do not break this protocol.

### Session State Keys
`app.py` uses these `st.session_state` keys — do not rename them:

| Key                    | Type   | Description                            |
|------------------------|--------|----------------------------------------|
| `meal_messages`        | list   | Chat history for Meal Planner tab      |
| `shop_messages`        | list   | Chat history for Price Finder tab      |
| `active_notes`         | str    | Prepended Apple Notes context string   |
| `active_note_names`    | list   | Display names of loaded notes          |
| `note_titles`          | list   | All available Apple Note titles        |
| `current_session_id`   | str    | Timestamp key for current session      |
| `session_title`        | str    | LLM-generated title for current chat   |

### Notes Context Injection
When `active_notes` is set, its content is prepended to the user message string before it is passed to any agent. Agents do not know about this — it is handled entirely in `app.py` before the `run()` call.

### Location Guard
`location_guard()` in `app.py` blocks all agent calls if the sidebar location field is empty. Agents can assume location is a non-empty string.

---

## How to Run

```bash
pip install -r requirements.txt
streamlit run app.py
```

Requires `.streamlit/secrets.toml`:
```toml
GROQ_API_KEY = "gsk_..."
KROGER_CLIENT_ID = "..."
KROGER_CLIENT_SECRET = "..."
```

---

## Known Issues & Improvement Areas

- `chat_history.json` is committed to the repo — it should be in `.gitignore`
- Flat-file persistence will break under concurrent users; set `DATABASE_URL` for DB-backed persistence
- `apple_notes.py` is macOS-only; `is_available()` returns `False` on other platforms — no fallback UI exists
- No tests exist anywhere in the project
- Kroger API errors are not surfaced to the user (silent failures)
- No `README.md` — setup instructions only exist in this file

---

## What Codex Should NOT Change

- The `yield ("status", ...) / yield ("done", ...)` generator protocol in agents
- The `st.secrets` pattern for loading API keys (do not switch to `os.environ`)
- The `MEAL_PLAN_PROMPT` and `SHOPPING_SYNTHESIS_PROMPT` templates in `config.py` without explicit instruction — they are tuned
- The Streamlit tab structure (`tab_meal`, `tab_shop`) and the two-message-list pattern
