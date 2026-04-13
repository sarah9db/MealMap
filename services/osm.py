import math
import requests

_HEADERS = {"User-Agent": "MiseApp/1.0 (personal project)"}


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


def geocode(location: str) -> tuple[float, float] | None:
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": location, "format": "json", "limit": 1},
            headers=_HEADERS,
            timeout=10,
        )
        results = resp.json()
        if results:
            return float(results[0]["lat"]), float(results[0]["lon"])
    except Exception:
        pass
    return None


def find_nearby_stores(lat: float, lng: float, radius_m: int = 5000) -> list[dict]:
    """Return grocery stores within radius_m metres, sorted by distance."""
    query = f"""
    [out:json][timeout:20];
    (
      node["shop"~"supermarket|grocery|department_store|wholesale|variety_store|discount|general"](around:{radius_m},{lat},{lng});
      way["shop"~"supermarket|grocery|wholesale|variety_store|discount|general"](around:{radius_m},{lat},{lng});
      node["brand"~"Walmart|Aldi|Costco|Target|Whole Foods|Trader Joe|Sprouts|Smart & Final|Food 4 Less|Stater Bros|WinCo|H Mart|Vallarta"](around:{radius_m},{lat},{lng});
      way["brand"~"Walmart|Aldi|Costco|Target|Whole Foods|Trader Joe|Sprouts|Smart & Final|Food 4 Less|Stater Bros|WinCo|H Mart|Vallarta"](around:{radius_m},{lat},{lng});
    );
    out center;
    """
    try:
        resp = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": query},
            headers=_HEADERS,
            timeout=20,
        )
        elements = resp.json().get("elements", [])
        seen: dict[str, dict] = {}
        for el in elements:
            name = el.get("tags", {}).get("name", "").strip()
            if not name:
                continue
            elat = el.get("lat") or el.get("center", {}).get("lat")
            elng = el.get("lon") or el.get("center", {}).get("lon")
            if not (elat and elng):
                continue
            dist = haversine_km(lat, lng, float(elat), float(elng))
            key = name.lower()
            if key not in seen or dist < seen[key]["distance_km"]:
                seen[key] = {"name": name, "distance_km": round(dist, 2)}
        return sorted(seen.values(), key=lambda x: x["distance_km"])
    except Exception:
        return []
