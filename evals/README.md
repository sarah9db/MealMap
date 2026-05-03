## Maple evals

This folder adds a lightweight eval harness for Maple’s two agents:

- Meal planner: `agents/meal_agent.py`
- Price finder: `agents/shopping_agent.py`

It supports:

- Multi-turn conversations (so you can test “don’t repeat declined recipes”)
- Deterministic checks (required text, banned text, no-repeat-declined)
- Hallucination checks for unsupported ingredients, stores, and prices
- Optional LLM judge scoring (off by default)
- Groq model discovery via `GET /openai/v1/models` and model sweeps
- A mocked “Price finder” mode (so external APIs don’t make evals flaky)
- MMLU-style standardized multiple-choice evals from `mmlu.csv`
- Direct vision extraction evals for ingredient photos and grocery-list images

### Prereqs

- Install deps: `pip install -r requirements.txt`
- Ensure `.streamlit/secrets.toml` exists with at least:

```toml
GROQ_API_KEY = "gsk_..."
```

### Quick start

List models your Groq key can access:

```bash
python3 evals/run_evals.py --list-models
```

Run the included smoke evals against the current `config.TEXT_MODEL`:

```bash
python3 evals/run_evals.py --cases evals/cases_smoke.jsonl
```

Sweep several models (comma-separated) on meal evals:

```bash
python3 evals/run_evals.py \
  --cases evals/cases_smoke.jsonl \
  --agent meal \
  --models llama-3.1-8b-instant,llama-3.3-70b-versatile,openai/gpt-oss-20b
```

Add judge scoring (more subjective, slower/paid):

```bash
python3 evals/run_evals.py --judge --judge-model llama-3.1-8b-instant
```

After a sweep, automatically write the best `TEXT_MODEL` into `config.py`:

```bash
python3 evals/run_evals.py \
  --agent meal \
  --models llama-3.1-8b-instant,llama-3.3-70b-versatile \
  --apply-best-text-model
```

Run a standardized MMLU sample against the current `config.TEXT_MODEL`:

```bash
python3 evals/run_evals.py --standard-eval mmlu --mmlu-limit 100
```

Sweep Groq models on the same MMLU sample:

```bash
python3 evals/run_evals.py \
  --standard-eval mmlu \
  --mmlu-limit 100 \
  --models llama-3.1-8b-instant,llama-3.3-70b-versatile,openai/gpt-oss-20b
```

Sweep visible Groq models and apply the best MMLU scorer to `TEXT_MODEL`:

```bash
python3 evals/run_evals.py \
  --standard-eval mmlu \
  --mmlu-limit 100 \
  --all-visible-models \
  --apply-best-text-model
```

Filter MMLU to specific subjects:

```bash
python3 evals/run_evals.py \
  --standard-eval mmlu \
  --mmlu-subjects college_medicine,high_school_biology \
  --mmlu-limit 50
```

Run direct vision evals:

```bash
python3 evals/run_evals.py --standard-eval vision --vision-cases evals/cases_vision.jsonl
```

Compare vision models:

```bash
python3 evals/run_evals.py \
  --standard-eval vision \
  --vision-cases evals/cases_vision.jsonl \
  --models llama-3.2-11b-vision-preview,other-vision-model-id
```

Run Price Finder evals in mock mode (no external network):

```bash
python3 evals/run_evals.py --agent shop --mock-shop
```

### Case schema (jsonl)

Each line is one JSON object:

- `id` (string): unique case id
- `agent` (string): `meal` or `shop`
- `location` (string): zip or address-like string
- `active_notes` (string, optional): notes context injected into each user turn
- `turns` (array): list of either strings, or objects like `{ "text": "...", "image_path": "/abs/path.jpg" }`
- `declines` (array, optional): list of `{ "after_turn": 1, "titles": ["chicken fried rice"] }`
  - `after_turn` is 1-based and refers to the user turn after which the decline applies
- `assert` (object, optional):
  - `must_include_any`: array of strings (at least 1 must appear)
  - `must_not_include`: array of strings (none may appear)
  - `min_chars`: integer minimum answer length
  - `no_repeat_declined`: boolean
  - `allowed_ingredients`: array of user-provided foods; common food mentions outside this list and pantry staples fail the case
  - `allowed_pantry`: optional override for pantry/spice items allowed in addition to `allowed_ingredients`
  - `ingredient_watchlist`: optional override for the food terms scanned by the hallucination check
  - `allowed_stores`: allowed store names for shopping output
  - `store_watchlist`: optional override for store names scanned by the hallucination check
  - `allowed_prices`: allowed dollar prices for shopping output

### Vision Case Schema

Each line in `evals/cases_vision.jsonl` is one JSON object:

- `id` (string): unique case id
- `image_path` (string): absolute path or repo-relative path to `.jpg`, `.png`, or `.webp`
- `mode` (string): `ingredients` for food photos, or `grocery_list` for list extraction
- `assert` (object):
  - `must_include_any`: visible ingredients/items the output should include
  - `must_not_include`: items the model should not hallucinate
  - `allowed_ingredients`: exact visible/allowed foods; common food mentions outside this list fail the case
  - `min_chars`: minimum output length

### Files

- `evals/cases_smoke.jsonl`: small starter cases
- `evals/run_evals.py`: main runner + model sweep
- `evals/scoring.py`: deterministic checks (includes no-repeat-declined)
- `evals/groq_models.py`: model listing helper
- `evals/mock_shop.py`: monkeypatches external calls for shopping evals
- `evals/cases_vision.jsonl`: starter file for direct vision eval cases
- `mmlu.csv`: MMLU-style standardized multiple-choice questions
