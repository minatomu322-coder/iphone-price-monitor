from __future__ import annotations

from typing import Any

import requests

from ..models import Observation, Product
from .base import polite_sleep, timeouts


# 楽天ウェブサービス 楽天市場商品検索API（要 applicationId）
ENDPOINT = "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20220601"


class RakutenSource:
    """楽天市場の販売価格（buy側）。公式APIなので安定・規約上も安全。"""

    name = "rakuten"

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
            "applicationId": self.app_id,
            "format": "json",
            "keyword": product.keyword,
            "hits": int(self.settings.get("hits", 30)),
            "sort": "+itemPrice",
            "availability": 1,
            "imageFlag": 0,
        }
        response = self.session.get(ENDPOINT, params=params, timeout=timeouts(self.scraping))
        response.raise_for_status()
        return parse_items(response.json(), product, self.settings)


def parse_items(payload: dict[str, Any], product: Product, settings: dict[str, Any]) -> list[Observation]:
    """APIレスポンスから、対象商品に合致する最安リスティングを抽出する。"""
    postage_estimate = int(settings.get("postage_estimate", 300))
    max_listings = int(settings.get("max_listings", 5))
    found: list[Observation] = []
    for entry in payload.get("Items", []):
        item = entry.get("Item", entry)
        name = str(item.get("itemName", ""))
        if not product.matches(name):
            continue
        try:
            price = int(item.get("itemPrice"))
        except (TypeError, ValueError):
            continue
        # postageFlag: 0=送料込み, 1=送料別 → 別なら見積もりを加算して「送料込みの仕入価格」に揃える
        if int(item.get("postageFlag", 0) or 0) == 1:
            price += postage_estimate
        found.append(
            Observation(
                product_id=product.product_id,
                source="rakuten",
                side="buy",
                channel="rakuten",
                price=price,
                url=str(item.get("itemUrl", "")),
                condition="",
                stock=None,
                raw_text=f"{item.get('shopName', '')} | {name}",
            )
        )
    found.sort(key=lambda obs: obs.price)
    return found[:max_listings]
