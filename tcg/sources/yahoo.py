from __future__ import annotations

from typing import Any

import requests

from ..models import Observation, Product
from .base import polite_sleep, timeouts


# Yahoo!ショッピング 商品検索API v3（要 appid＝Client ID）
ENDPOINT = "https://shopping.yahooapis.jp/ShoppingWebService/V3/itemSearch"


class YahooShoppingSource:
    """Yahoo!ショッピングの販売価格（buy側）。公式APIなので安定・規約上も安全。"""

    name = "yahoo_shopping"

    def __init__(
        self,
        app_id: str,
        session: requests.Session,
        settings: dict[str, Any],
        scraping: dict[str, Any],
    ) -> None:
        self.app_id = app_id
        self.session = session
        self.settings = settings
        self.scraping = scraping

    def fetch(self, product: Product) -> list[Observation]:
        polite_sleep(float(self.settings.get("request_delay_seconds", 1)))
        params = {
            "appid": self.app_id,
            "query": product.keyword,
            "results": int(self.settings.get("hits", 30)),
            "sort": "+price",
            "in_stock": "true",
        }
        response = self.session.get(ENDPOINT, params=params, timeout=timeouts(self.scraping))
        response.raise_for_status()
        return parse_hits(response.json(), product, self.settings)


def parse_hits(payload: dict[str, Any], product: Product, settings: dict[str, Any]) -> list[Observation]:
    postage_estimate = int(settings.get("postage_estimate", 300))
    max_listings = int(settings.get("max_listings", 5))
    found: list[Observation] = []
    for hit in payload.get("hits", []):
        name = str(hit.get("name", ""))
        if not product.matches(name):
            continue
        try:
            price = int(hit.get("price"))
        except (TypeError, ValueError):
            continue
        # shipping.code: 1=送料無料 / それ以外は送料別（条件付き含む）とみなし見積もりを加算
        shipping = hit.get("shipping") or {}
        if int(shipping.get("code", 0) or 0) != 1:
            price += postage_estimate
        seller = (hit.get("seller") or {}).get("name", "")
        found.append(
            Observation(
                product_id=product.product_id,
                source="yahoo_shopping",
                side="buy",
                channel="yahoo_shopping",
                price=price,
                url=str(hit.get("url", "")),
                raw_text=f"{seller} | {name}",
            )
        )
    found.sort(key=lambda obs: obs.price)
    return found[:max_listings]
