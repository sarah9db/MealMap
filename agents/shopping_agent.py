import re
import time

from config import TEXT_MODEL, SHOPPING_SYNTHESIS_PROMPT
from services import osm, trader_joes, kroger, flipp


_DEFAULT_ITEMS = ["eggs", "rice", "oats", "peanut butter", "chicken breast", "pasta", "olive oil"]
_DEFAULT_STORES = ["Ralphs", "Trader Joe's", "Whole Foods"]


def _extract_items(client, user_message: str) -> list[str]:
    """Ask LLM to pull grocery items from the user's message."""
    try:
        resp = client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{
                "role": "user",
                "content": (
                    "Extract the grocery items the user wants to find prices for. "
                    "Return one item per line, no explanations, no bullet points.\n\n"
                    + user_message
                ),
            }],
        )
        items = []
        for line in resp.choices[0].message.content.splitlines():
            item = re.sub(r"^[\-\*\d\.\s]+", "", line).strip()
            if item:
                items.append(item)
        return items[:10] or _DEFAULT_ITEMS
    except Exception:
        return _DEFAULT_ITEMS


def run(client, location: str, user_message: str, kroger_client_id: str = "", kroger_client_secret: str = ""):
    # ── Step 1: geocode + find nearby stores ─────────────────────────────────
    yield ("status", f"Locating {location}...")
    coords = osm.geocode(location)

    stores_with_dist: list[dict] = []
    if coords:
        yield ("status", "Finding nearby grocery stores...")
        stores_with_dist = osm.find_nearby_stores(coords[0], coords[1], radius_m=5000)[:8]

    if stores_with_dist:
        store_names = [s["name"] for s in stores_with_dist]
        stores_summary = "\n".join(f"{s['name']} ({s['distance_km']} km)" for s in stores_with_dist)
    else:
        store_names = _DEFAULT_STORES
        stores_summary = "\n".join(_DEFAULT_STORES)

    yield ("status", f"Found {len(store_names)} stores nearby")

    # ── Step 2: extract items ────────────────────────────────────────────────
    yield ("status", "Reading your list...")
    items = _extract_items(client, user_message)

    zip_code = re.sub(r"[^\d]", "", location) or location
    all_results: list[str] = []
    found_items: set[str] = set()  # tracks items that got at least one real price

    # ── Step 3: Trader Joe's (direct GraphQL API) ────────────────────────────
    yield ("status", "Querying Trader Joe's...")
    for item in items:
        result = trader_joes.search(item)
        all_results.append(f"=== {item} at Trader Joe's ===\n{result}")
        if "price not listed" not in result and "failed" not in result and "No Trader" not in result:
            found_items.add(item)
        time.sleep(0.2)

    # ── Step 4: Flipp (other stores' flyers) ────────────────────────────────
    for item in items:
        yield ("status", f"Checking flyers: {item}...")
        raw = flipp.search(item, zip_code)
        if raw:
            formatted = flipp.format_results(raw, target_stores=store_names)
            if not formatted:
                formatted = flipp.format_results(raw)  # no store filter fallback
            if formatted:
                all_results.append(f"=== {item} (store flyers) ===\n{formatted}")
                found_items.add(item)
        time.sleep(0.3)

    # ── Step 5: Kroger/Ralphs API for items still missing prices ────────────
    missing_items = [i for i in items if i not in found_items]
    if kroger_client_id and kroger_client_secret and missing_items:
        yield ("status", "Checking Ralphs for missing items...")
        token = kroger.get_token(kroger_client_id, kroger_client_secret)
        if token:
            location_id = kroger.get_location_id(token, zip_code)
            if location_id:
                for item in missing_items:
                    yield ("status", f"Ralphs: {item}...")
                    result = kroger.search_products(token, location_id, item)
                    all_results.append(f"=== {item} at Ralphs (live) ===\n{result}")

    # ── Step 6: LLM synthesis ────────────────────────────────────────────────
    yield ("status", "Building price comparison...")
    synthesis_prompt = SHOPPING_SYNTHESIS_PROMPT.format(
        location=location,
        stores_summary=stores_summary,
        price_data="\n\n".join(all_results),
    )
    resp = client.chat.completions.create(
        model=TEXT_MODEL,
        messages=[{"role": "user", "content": synthesis_prompt}],
    )
    yield ("done", resp.choices[0].message.content)
