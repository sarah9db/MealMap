import base64
import requests


def get_token(client_id: str, client_secret: str) -> str | None:
    try:
        creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        resp = requests.post(
            "https://api.kroger.com/v1/connect/oauth2/token",
            headers={
                "Authorization": f"Basic {creds}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data="grant_type=client_credentials&scope=product.compact",
            timeout=10,
        )
        return resp.json().get("access_token")
    except Exception:
        return None


def get_location_id(token: str, zip_code: str) -> str | None:
    try:
        resp = requests.get(
            "https://api.kroger.com/v1/locations",
            headers={"Authorization": f"Bearer {token}"},
            params={"filter.zipCode.near": zip_code, "filter.chain": "RALPHS", "filter.limit": 1},
            timeout=10,
        )
        data = resp.json().get("data", [])
        return data[0]["locationId"] if data else None
    except Exception:
        return None


def search_products(token: str, location_id: str, term: str) -> str:
    try:
        resp = requests.get(
            "https://api.kroger.com/v1/products",
            headers={"Authorization": f"Bearer {token}"},
            params={"filter.term": term, "filter.locationId": location_id, "filter.limit": 5},
            timeout=10,
        )
        products = resp.json().get("data", [])
        if not products:
            return f"No Ralphs results for '{term}'."
        lines = []
        for p in products:
            name = p.get("description", term)
            items = p.get("items", [{}])
            price_info = items[0].get("price", {}) if items else {}
            regular = price_info.get("regular")
            promo = price_info.get("promo")
            size = items[0].get("size", "") if items else ""
            if promo:
                price_str = f"${promo:.2f} (sale, reg ${regular:.2f})"
            elif regular:
                price_str = f"${regular:.2f}"
            else:
                price_str = "price not listed"
            lines.append(f"Ralphs: {name} — {price_str} / {size}".strip())
        return "\n".join(lines)
    except Exception as e:
        return f"Kroger API error: {e}"
