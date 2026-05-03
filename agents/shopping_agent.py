import re
import time

from config import TEXT_MODEL, SHOPPING_SYNTHESIS_PROMPT
from services import osm, kroger, flipp


_DEFAULT_ITEMS = ["eggs", "rice", "oats", "peanut butter", "chicken breast", "pasta", "olive oil"]
_DEFAULT_STORES = ["Ralphs", "Trader Joe's", "Whole Foods"]
_PRICE_RE = re.compile(r"\$\s*(\d+(?:\.\d{2})?)")


def _extract_items(client, user_message: str) -> list[str]:
    """Ask LLM to pull grocery items from the user's message."""
    simple_items = [
        re.sub(r"^[\-\*\d\.\s]+", "", part).strip()
        for part in re.split(r"[,;\n]", user_message)
    ]
    simple_items = [i for i in simple_items if i and len(i.split()) <= 4]
    if 1 <= len(simple_items) <= 10:
        return simple_items

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


def _distance_for_store(store: str, stores_with_dist: list[dict]) -> str:
    for s in stores_with_dist:
        name = str(s.get("name", ""))
        if name.lower() == store.lower():
            return f"{s.get('distance_km')} km"
    for s in stores_with_dist:
        name = str(s.get("name", ""))
        if name.lower() in store.lower() or store.lower() in name.lower():
            return f"{s.get('distance_km')} km"
    return "—"


def _parse_price_blocks(price_blocks: list[str]) -> dict[str, list[dict]]:
    parsed: dict[str, list[dict]] = {}
    current_item = ""
    for block in price_blocks:
        for raw_line in block.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            header = re.match(r"^===\s*(.*?)\s*(?:\(|at\b|===)", line)
            if header:
                current_item = header.group(1).strip()
                parsed.setdefault(current_item, [])
                continue
            if not current_item or ":" not in line or "—" not in line:
                continue
            store, rest = line.split(":", 1)
            name, price_part = rest.split("—", 1)
            price_match = _PRICE_RE.search(price_part)
            if not price_match:
                continue
            price = f"${float(price_match.group(1)):.2f}"
            size = price_part[price_match.end():].strip(" /")
            parsed.setdefault(current_item, []).append({
                "store": store.strip(),
                "name": name.strip(),
                "price": price,
                "price_num": float(price_match.group(1)),
                "size": size or "—",
            })
    return parsed


def _build_price_response(items: list[str], stores_with_dist: list[dict], price_blocks: list[str]) -> str:
    parsed = _parse_price_blocks(price_blocks)
    rows = ["| Item | Store | Price | Size | Distance |", "|------|-------|-------|------|----------|"]
    best_stores: dict[str, int] = {}

    for item in items:
        options = parsed.get(item, [])
        best = min(options, key=lambda r: r["price_num"]) if options else None
        if not best:
            rows.append(f"| {item} | — | — | — | — |")
            continue
        best_stores[best["store"]] = best_stores.get(best["store"], 0) + 1
        rows.append(
            f"| {item} | {best['store']} | {best['price']} | {best['size']} | "
            f"{_distance_for_store(best['store'], stores_with_dist)} |"
        )

    if best_stores:
        ranked = sorted(best_stores.items(), key=lambda kv: kv[1], reverse=True)
        store_text = ", ".join(store for store, _count in ranked[:2])
        best_section = f"Best stores: {store_text}."
    else:
        best_section = "Best stores: No exact nearby prices found for these items."

    return "\n".join(rows) + "\n\n" + best_section


def run(client, location: str, user_message: str, kroger_client_id: str = "", kroger_client_secret: str = ""):
    # ── Step 1: geocode + find nearby stores ─────────────────────────────────
    yield ("status", f"Locating {location}...")
    coords = osm.geocode(location)

    stores_with_dist: list[dict] = []
    if coords:
        yield ("status", "Finding nearby grocery stores...")
        stores_with_dist = osm.find_nearby_stores(coords[0], coords[1], radius_m=5000)[:15]

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

    # ── Step 3: Flipp (store flyers) ────────────────────────────────────────
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
    if all_results:
        yield ("done", _build_price_response(items, stores_with_dist, all_results))
        return

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
