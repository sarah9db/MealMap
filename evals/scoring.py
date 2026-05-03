from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any


def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _token_set(s: str) -> set[str]:
    return {t for t in _norm(s).split(" ") if t}


def _term_forms(term: str) -> set[str]:
    norm = _norm(term)
    forms = {norm}
    forms.update(t for t in norm.split() if len(t) > 2)
    if norm.endswith("ies") and len(norm) > 3:
        forms.add(norm[:-3] + "y")
    if norm.endswith("es") and len(norm) > 2:
        forms.add(norm[:-2])
    if norm.endswith("s") and len(norm) > 1:
        forms.add(norm[:-1])
    return {f for f in forms if f}


def _similar(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


_TITLE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^\s{0,3}#{1,6}\s+(?P<t>.+?)\s*$"),
    re.compile(r"^\s{0,3}\*\*(?P<t>[^*]{3,80})\*\*\s*(?:[:\-–—].*)?$"),
    re.compile(r"^\s{0,3}(?:\d+[\.\)]|\-|\*)\s+(?P<t>[^:\-–—]{3,80})\s*(?:[:\-–—].*)?$"),
]


def extract_title_candidates(markdown: str) -> list[str]:
    out: list[str] = []
    for line in markdown.splitlines():
        for pat in _TITLE_PATTERNS:
            m = pat.match(line)
            if m:
                title = m.group("t").strip()
                if 3 <= len(title) <= 120:
                    out.append(title)
                break
    # Keep it small and de-dupe, preserving order.
    seen: set[str] = set()
    uniq: list[str] = []
    for t in out:
        key = _norm(t)
        if key and key not in seen:
            seen.add(key)
            uniq.append(t)
    return uniq[:50]


@dataclass
class CheckResult:
    name: str
    ok: bool
    details: str = ""


DEFAULT_ALLOWED_PANTRY = [
    "salt",
    "pepper",
    "black pepper",
    "oil",
    "olive oil",
    "vegetable oil",
    "water",
    "butter",
    "chili",
    "chili flakes",
    "chili powder",
    "cayenne",
    "jalapeno",
    "jalapenos",
    "harissa",
    "gochujang",
    "hot sauce",
    "paprika",
    "cumin",
    "garlic powder",
    "onion powder",
]


# A deliberately conservative watchlist. These are common meal-plan ingredients
# where an unsupported mention usually means the model invented food.
DEFAULT_INGREDIENT_WATCHLIST = [
    "almonds",
    "apple",
    "apples",
    "avocado",
    "bacon",
    "banana",
    "bananas",
    "beans",
    "beef",
    "bell pepper",
    "bell peppers",
    "bread",
    "broccoli",
    "carrot",
    "carrots",
    "cheddar",
    "cheese",
    "chicken",
    "chickpeas",
    "cilantro",
    "coconut milk",
    "corn",
    "cream",
    "egg",
    "eggs",
    "feta",
    "flour",
    "ginger",
    "green onion",
    "green onions",
    "ground turkey",
    "honey",
    "kale",
    "lentils",
    "lettuce",
    "lime",
    "limes",
    "milk",
    "mushroom",
    "mushrooms",
    "noodles",
    "oats",
    "onion",
    "onions",
    "pasta",
    "peanut butter",
    "peas",
    "pork",
    "potato",
    "potatoes",
    "quinoa",
    "rice",
    "salmon",
    "salsa",
    "sausage",
    "shrimp",
    "soy sauce",
    "spinach",
    "steak",
    "sweet potato",
    "sweet potatoes",
    "tofu",
    "tomato",
    "tomatoes",
    "tortilla",
    "tortillas",
    "turkey",
    "yogurt",
    "zucchini",
]


DEFAULT_STORE_WATCHLIST = [
    "Aldi",
    "Costco",
    "Kroger",
    "Ralphs",
    "Safeway",
    "Target",
    "Trader Joe's",
    "Vons",
    "Walmart",
    "Whole Foods",
]


def check_must_include_any(text: str, phrases: list[str]) -> CheckResult:
    if not phrases:
        return CheckResult(name="must_include_any", ok=True)
    low = text.lower()
    ok = any(p.lower() in low for p in phrases)
    return CheckResult(
        name="must_include_any",
        ok=ok,
        details="" if ok else f"missing all of: {phrases}",
    )


def check_must_not_include(text: str, phrases: list[str]) -> CheckResult:
    if not phrases:
        return CheckResult(name="must_not_include", ok=True)
    low = text.lower()
    hits = [p for p in phrases if p.lower() in low]
    ok = not hits
    return CheckResult(
        name="must_not_include",
        ok=ok,
        details="" if ok else f"found banned phrases: {hits}",
    )


def check_min_chars(text: str, n: int) -> CheckResult:
    if not n:
        return CheckResult(name="min_chars", ok=True)
    ok = len(text.strip()) >= n
    return CheckResult(
        name="min_chars",
        ok=ok,
        details="" if ok else f"got {len(text.strip())} chars, need {n}",
    )


def check_allowed_ingredients(
    text: str,
    allowed_ingredients: list[str],
    allowed_pantry: list[str] | None = None,
    watchlist: list[str] | None = None,
) -> CheckResult:
    if not allowed_ingredients:
        return CheckResult(name="allowed_ingredients", ok=True)

    allowed_forms: set[str] = set()
    for term in [*allowed_ingredients, *(allowed_pantry or DEFAULT_ALLOWED_PANTRY)]:
        allowed_forms.update(_term_forms(term))

    found: list[str] = []
    norm_text = f" {_norm(text)} "
    for term in watchlist or DEFAULT_INGREDIENT_WATCHLIST:
        forms = _term_forms(term)
        if forms & allowed_forms:
            continue
        if any(f" {form} " in norm_text for form in forms):
            found.append(term)

    # De-dupe equivalent singular/plural hits while preserving useful details.
    unique: list[str] = []
    seen: set[str] = set()
    for term in found:
        key = sorted(_term_forms(term))[0]
        if key not in seen:
            seen.add(key)
            unique.append(term)

    return CheckResult(
        name="allowed_ingredients",
        ok=not unique,
        details="" if not unique else f"possible unsupported ingredients: {unique}",
    )


def check_allowed_phrases(
    text: str,
    allowed: list[str],
    watchlist: list[str],
    name: str,
) -> CheckResult:
    if not allowed:
        return CheckResult(name=name, ok=True)

    allowed_norms = {_norm(item) for item in allowed if item and item.strip()}
    norm_text = f" {_norm(text)} "
    found = []
    for item in watchlist:
        item_norm = _norm(item)
        if not item_norm or item_norm in allowed_norms:
            continue
        if f" {item_norm} " in norm_text:
            found.append(item)

    return CheckResult(
        name=name,
        ok=not found,
        details="" if not found else f"unsupported mentions: {found}",
    )


def check_allowed_prices(text: str, allowed_prices: list[str]) -> CheckResult:
    if not allowed_prices:
        return CheckResult(name="allowed_prices", ok=True)

    allowed = {p.strip() for p in allowed_prices if p and p.strip()}
    found = re.findall(r"\$\s*\d+(?:\.\d{2})?", text)
    unsupported = []
    for price in found:
        canonical = re.sub(r"\s+", "", price)
        if canonical not in allowed and price not in allowed:
            unsupported.append(price)

    return CheckResult(
        name="allowed_prices",
        ok=not unsupported,
        details="" if not unsupported else f"unsupported prices: {unsupported}",
    )


def check_no_repeat_declined(
    assistant_texts_after_decline: list[str],
    declined_titles: list[str],
    fuzzy_threshold: float = 0.86,
) -> CheckResult:
    declined_titles = [t for t in declined_titles if t and t.strip()]
    if not declined_titles:
        return CheckResult(name="no_repeat_declined", ok=True)

    declined_norms = [_norm(t) for t in declined_titles]
    declined_tokens = [_token_set(t) for t in declined_titles]

    for assistant_text in assistant_texts_after_decline:
        low = _norm(assistant_text)
        if any(dn and dn in low for dn in declined_norms):
            return CheckResult(
                name="no_repeat_declined",
                ok=False,
                details="assistant repeated declined recipe title (substring match)",
            )

        candidates = extract_title_candidates(assistant_text)
        for cand in candidates:
            for i, declined in enumerate(declined_titles):
                if _similar(cand, declined) >= fuzzy_threshold:
                    return CheckResult(
                        name="no_repeat_declined",
                        ok=False,
                        details=f"assistant repeated declined recipe (fuzzy match): '{cand}' ~ '{declined}'",
                    )
                # token overlap as a second heuristic (helps with punctuation/word order)
                ct = _token_set(cand)
                dt = declined_tokens[i]
                if dt and ct:
                    overlap = len(ct & dt) / max(1, len(dt))
                    if overlap >= 0.85 and len(dt) >= 2:
                        return CheckResult(
                            name="no_repeat_declined",
                            ok=False,
                            details=f"assistant repeated declined recipe (token overlap): '{cand}' ~ '{declined}'",
                        )

    return CheckResult(name="no_repeat_declined", ok=True)


def score_case(case: dict[str, Any], conversation: list[dict[str, str]]) -> dict[str, Any]:
    """
    Score the final assistant output with deterministic checks.

    `conversation` is a list of {"role": "...", "content": "..."} in chronological order.
    """
    asserts = case.get("assert", {}) or {}
    decls = case.get("declines", []) or []

    assistant_texts = [m["content"] for m in conversation if m["role"] == "assistant"]
    final_text = assistant_texts[-1] if assistant_texts else ""

    checks: list[CheckResult] = []
    checks.append(check_must_include_any(final_text, asserts.get("must_include_any", []) or []))
    checks.append(check_must_not_include(final_text, asserts.get("must_not_include", []) or []))
    checks.append(check_min_chars(final_text, int(asserts.get("min_chars", 0) or 0)))
    checks.append(check_allowed_ingredients(
        final_text,
        asserts.get("allowed_ingredients", []) or [],
        asserts.get("allowed_pantry", None),
        asserts.get("ingredient_watchlist", None),
    ))
    checks.append(check_allowed_phrases(
        final_text,
        asserts.get("allowed_stores", []) or [],
        asserts.get("store_watchlist", DEFAULT_STORE_WATCHLIST),
        "allowed_stores",
    ))
    checks.append(check_allowed_prices(final_text, asserts.get("allowed_prices", []) or []))

    if asserts.get("no_repeat_declined"):
        # Build the set of assistant messages that occur AFTER each decline marker.
        after_texts: list[str] = []
        declined_titles: list[str] = []
        for d in decls:
            after_turn = int(d.get("after_turn", 0) or 0)
            titles = d.get("titles", []) or []
            # Turn indices are 1-based in the jsonl: after_turn=1 means "after the first user turn".
            # In our conversation list, each turn adds user+assistant, so approximate the cut:
            # after_turn user turns => after_turn assistants.
            start_idx = max(0, after_turn)  # assistant count to skip
            after_texts.extend(assistant_texts[start_idx:])
            declined_titles.extend(titles)

        checks.append(check_no_repeat_declined(after_texts, declined_titles))

    ok = all(c.ok for c in checks)
    return {
        "ok": ok,
        "checks": [c.__dict__ for c in checks],
    }


def dumps_json(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)
