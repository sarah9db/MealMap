import requests

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def search(item: str, zip_code: str) -> list[dict]:
    """Return raw Flipp items from store flyers near zip_code."""
    try:
        resp = requests.get(
            "https://backflipp.wishabi.com/flipp/items/search",
            params={"q": item, "locale": "en-us", "postal_code": zip_code},
            headers=_HEADERS,
            timeout=10,
        )
        return resp.json().get("items", [])
    except Exception:
        return []


def format_results(items: list[dict], target_stores: list[str] | None = None) -> str:
    """Format Flipp results into price lines, optionally filtered by store name."""
    lines = []
    seen: set[str] = set()
    for item in items:
        store = item.get("merchant_name") or item.get("retailer_name", "Unknown store")
        if target_stores:
            if not any(s.lower() in store.lower() or store.lower() in s.lower() for s in target_stores):
                continue
        name = item.get("name") or item.get("display_name", "")
        price = item.get("current_price") or item.get("price")
        price_text = item.get("price_text", "")
        size = item.get("unit_of_measure") or item.get("description") or ""
        key = f"{store}|{name}"
        if key in seen:
            continue
        seen.add(key)
        price_str = f"${price:.2f}" if isinstance(price, (int, float)) else (price_text or "see flyer")
        lines.append(f"{store}: {name} — {price_str} {size}".strip())
        if len(lines) >= 15:
            break
    return "\n".join(lines)
