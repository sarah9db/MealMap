import requests

_URL = "https://www.traderjoes.com/api/graphql"

# User-Agent required — TJ's returns 403 without a browser UA
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Accept": "application/json",
}

_QUERY = """
query SearchProducts($search: String!) {
  searchProducts(
    channel: "website"
    published: "1"
    search: $search
    currentPage: 1
    pageSize: 8
    lang: "en_US"
  ) {
    items {
      item_title
      retail_price
      sales_size
      sales_uom_description
    }
  }
}
"""


def search(item: str) -> str:
    try:
        resp = requests.post(
            _URL,
            headers=_HEADERS,
            json={"query": _QUERY, "variables": {"search": item}},
            timeout=10,
        )
        products = resp.json().get("data", {}).get("searchProducts", {}).get("items", [])
        if not products:
            return f"No Trader Joe's results for '{item}'."
        lines = []
        for p in products[:5]:
            name = p.get("item_title", item)
            price = p.get("retail_price")
            size = p.get("sales_size", "")
            uom = p.get("sales_uom_description", "")
            price_str = f"${float(price):.2f}" if price else "price not listed"
            lines.append(f"Trader Joe's: {name} — {price_str} / {size} {uom}".strip())
        return "\n".join(lines)
    except Exception as e:
        return f"Trader Joe's lookup failed: {e}"
