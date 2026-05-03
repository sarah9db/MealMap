from __future__ import annotations

import json
from typing import Any


_RUBRIC = {
    "helpfulness": "Is the answer actionable and useful for the user request?",
    "faithfulness": "Does it follow the user constraints (ingredients, declines, location, notes)?",
    "formatting": "Is it clearly structured and easy to read (tables, headings, bullets as appropriate)?",
    "specificity": "Does it include concrete quantities/steps/prices rather than vague advice?",
}


def judge_conversation(
    client,
    judge_model: str,
    case: dict[str, Any],
    conversation: list[dict[str, str]],
) -> dict[str, Any]:
    """
    LLM-as-judge scoring. Returns a JSON dict with 0-5 scores and any violations.

    Off by default in the runner to keep evals cheap and deterministic.
    """
    agent = case.get("agent", "")
    turns = case.get("turns", [])
    asserts = case.get("assert", {}) or {}
    declines = case.get("declines", []) or []

    final_assistant = ""
    for m in reversed(conversation):
        if m.get("role") == "assistant":
            final_assistant = m.get("content", "")
            break

    prompt = {
        "role": "user",
        "content": (
            "You are grading an assistant response for a meal-planning / grocery-pricing app.\n"
            "Return ONLY a JSON object with these keys:\n"
            "- scores: object with integer 0-5 for each: helpfulness, faithfulness, formatting, specificity\n"
            "- hallucinated_ingredients: array of ingredients used or recommended that are not supported by the user turns, active notes, or deterministic asserts\n"
            "- unsupported_price_or_store_claims: array of price/store/product claims not grounded in provided data\n"
            "- batch_prep_violation: boolean, true if a meal plan assigns many unrelated meals instead of batch-cooked repeats\n"
            "- violations: array of short strings (empty if none)\n"
            "- summary: 1 short sentence\n\n"
            f"Agent: {agent}\n"
            f"User turns: {json.dumps(turns, ensure_ascii=False)}\n"
            f"Declines: {json.dumps(declines, ensure_ascii=False)}\n"
            f"Deterministic asserts: {json.dumps(asserts, ensure_ascii=False)}\n\n"
            "Hallucination guidance:\n"
            "- If deterministic asserts include allowed_ingredients, treat those plus allowed_pantry/basic pantry staples as the source of truth.\n"
            "- For meal plans, flag any ingredient that is used as part of a recipe but was not provided or allowed.\n"
            "- For shopping answers, flag any concrete store, product, price, size, or discount that is not grounded in supplied price data.\n"
            "- Do not flag clearly optional add-ons if they are explicitly labeled as optional shopping suggestions.\n\n"
            "Rubric:\n"
            + "\n".join(f"- {k}: {v}" for k, v in _RUBRIC.items())
            + "\n\n"
            "Assistant final answer to grade:\n"
            + final_assistant
        ),
    }

    resp = client.chat.completions.create(
        model=judge_model,
        response_format={"type": "json_object"},
        messages=[prompt],
    )
    text = resp.choices[0].message.content
    try:
        return json.loads(text)
    except Exception:
        return {
            "scores": {"helpfulness": 0, "faithfulness": 0, "formatting": 0, "specificity": 0},
            "violations": ["judge_invalid_json"],
            "summary": "Judge returned invalid JSON.",
            "raw": text,
        }
