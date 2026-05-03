# AGENTS.md — services/

This directory contains thin integration wrappers for external data sources. These modules are **not agents** — they do not call the LLM. They are pure data-fetching utilities called from `app.py` or from agent modules.

All functions must be stateless and side-effect-free (no `st.*` calls, no session state access).

---

## vision.py

Handles image encoding and ingredient extraction using Groq's vision model.

### `encode_image(uploaded_file) -> tuple[str, str]`

Encodes a Streamlit `UploadedFile` as base64.

- **Returns:** `(base64_string, mime_type)` e.g. `("iVBOR...", "image/png")`
- Input is a Streamlit `UploadedFile` object (has `.read()` and `.type`)
- Must support: `jpg`, `jpeg`, `png`, `webp`

### `analyze_ingredients(client, b64: str, mime: str) -> str`

Calls Groq vision to identify ingredients in the image.

- **Model:** `VISION_MODEL` from `config.py` (`llama-3.2-11b-vision-preview`)
- **Returns:** A plain-text string describing identified ingredients (e.g. `"eggs, bell peppers, onions, chicken thighs"`)
- This string is injected into the meal agent's user message as `"Ingredients from image: {ingredients}"`
- Keep the prompt inside this function focused: ask only for ingredient identification, not recipes

**Do not change the return type** — `app.py` concatenates it into a string directly.

---

## apple_notes.py

macOS-only integration with the Apple Notes app via AppleScript.

### `is_available() -> bool`

Returns `True` only on macOS where the `osascript` binary is present. This is the gating check used in `app.py` before rendering any Notes UI.

**Do not raise exceptions** — return `False` gracefully on non-macOS platforms.

### `get_titles() -> list[str]`

Returns a list of all note titles from the default Apple Notes account.

- Uses `subprocess` + `osascript`
- Returns `[]` on failure (do not raise)

### `get_content(title: str) -> str`

Returns the plain-text body of the note with the given title.

- Returns `""` on failure (do not raise)
- Content is displayed to the user and injected into agent context — keep it as plain text, strip any HTML if AppleScript returns markup

---

## Adding a New Service

New services should follow these rules:

1. **No LLM calls** — services fetch or encode data only; agents do the reasoning
2. **No Streamlit imports** — services are UI-agnostic
3. **Never raise to the caller** — catch exceptions internally and return safe empty values (`""`, `[]`, `False`)
4. **Document your public functions** in this file with signature, return type, and failure behavior
5. Wire the import into `app.py` at the top alongside the existing service imports

### Example stubs for future services

```python
# services/walmart.py
def search_prices(query: str, zip_code: str) -> list[dict]:
    """Returns list of {item, price, unit, store} dicts. Returns [] on error."""
    ...

# services/instacart.py  
def is_available() -> bool: ...
def get_cart() -> list[str]: ...
```
