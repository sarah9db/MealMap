from __future__ import annotations

from typing import Callable


def apply_mock_shop() -> Callable[[], None]:
    """
    Monkeypatch external network calls used by `agents/shopping_agent.py` so evals are deterministic.

    Returns an undo() function that restores the original symbols.
    """
    from agents import shopping_agent
    from services import osm, flipp, kroger

    originals = {
        "osm.geocode": osm.geocode,
        "osm.find_nearby_stores": osm.find_nearby_stores,
        "flipp.search": flipp.search,
        "kroger.get_token": kroger.get_token,
        "kroger.get_location_id": kroger.get_location_id,
        "kroger.search_products": kroger.search_products,
        "shopping_agent.sleep": shopping_agent.time.sleep,
    }

    def _mock_geocode(_: str):
        return (37.789, -122.394)  # SF-ish

    def _mock_nearby_stores(_: float, __: float, radius_m: int = 5000):
        return [
            {"name": "Trader Joe's", "distance_km": 0.6},
            {"name": "Whole Foods", "distance_km": 1.2},
            {"name": "Ralphs", "distance_km": 2.4},
        ]

    def _mock_flipp_search(item: str, zip_code: str):
        item = item.lower().strip()
        if "egg" in item:
            return [{
                "merchant_name": "Trader Joe's",
                "name": "Large Eggs",
                "current_price": 2.99,
                "unit_of_measure": "12 ct",
            }]
        if "rice" in item:
            return [{
                "merchant_name": "Whole Foods",
                "name": "Jasmine Rice",
                "current_price": 3.49,
                "unit_of_measure": "2 lb",
            }]
        if "olive" in item:
            return [{
                "merchant_name": "Trader Joe's",
                "name": "Extra Virgin Olive Oil",
                "current_price": 7.99,
                "unit_of_measure": "1 L",
            }]
        return []

    def _mock_get_token(_: str, __: str):
        return None

    def _mock_get_location_id(_: str, __: str):
        return None

    def _mock_search_products(_: str, __: str, term: str):
        return f"No Ralphs results for '{term}'."

    osm.geocode = _mock_geocode
    osm.find_nearby_stores = _mock_nearby_stores
    flipp.search = _mock_flipp_search
    kroger.get_token = _mock_get_token
    kroger.get_location_id = _mock_get_location_id
    kroger.search_products = _mock_search_products
    shopping_agent.time.sleep = lambda *_args, **_kwargs: None

    def undo() -> None:
        osm.geocode = originals["osm.geocode"]
        osm.find_nearby_stores = originals["osm.find_nearby_stores"]
        flipp.search = originals["flipp.search"]
        kroger.get_token = originals["kroger.get_token"]
        kroger.get_location_id = originals["kroger.get_location_id"]
        kroger.search_products = originals["kroger.search_products"]
        shopping_agent.time.sleep = originals["shopping_agent.sleep"]

    return undo

