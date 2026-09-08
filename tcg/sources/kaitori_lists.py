from __future__ import annotations

from typing import Any

import requests
from bs4 import BeautifulSoup

from ..models import Observation, Product
from .base import parse_price, polite_sleep, timeouts
from .listing import Listing, debug_enabled, matches_product


class KaitoriListSource:
    """買取価格表（1ページに多数のカード）を、タイトルごとに1回だけ取得して照合する sell側ソース。

    サブクラスは `parse(html, url)` で Listing のリストを返す。
    """

    name = "kaitori_list"
    channel = "kaitori"

    def __init__(self, session: requests.Session, settings: dict[str, Any], scraping: dict[str, Any]) -> None:
        self.session = session
        self.settings = settings
        self.scraping = scraping
        self.urls: dict[str, str] = dict(settings.get("urls") or {})
        self._cache: dict[str, list[Listing]] = {}

    def parse(self, html: str, url: str) -> list[Listing]:  # pragma: no cover - サブクラスで実装
        raise NotImplementedError

    def listings_for(self, title: str) -> list[Listing]:
        url = self.urls.get(title)
        if not url:
            return []
        if title not in self._cache:
            polite_sleep(float(self.settings.get("request_delay_seconds", 3)))
            response = self.session.get(url, timeout=timeouts(self.scraping))
            response.raise_for_status()
            response.encoding = response.apparent_encoding or response.encoding
            self._cache[title] = self.parse(response.text, url)
            if debug_enabled():
                print(f"[debug] {self.name} {title}: {len(self._cache[title])}行 {url}")
        return self._cache[title]

    def fetch(self, product: Product) -> list[Observation]:
        url = self.urls.get(product.title)
        if not url:
            return []
        matched = [item for item in self.listings_for(product.title) if matches_product(product, item.text)]
        if not matched:
            return []
        # 同一カードの複数行（状態違い等）は、買取上限として最も高いものを採用
        best = max(matched, key=lambda item: item.price)
        if debug_enabled():
            print(f"[debug] {self.name} {product.product_id}: 一致 {len(matched)} → {best.price:,}円 {best.text[:70]}")
        return [
            Observation(
                product_id=product.product_id,
                source=self.name,
                side="sell",
                channel=self.channel,
                price=best.price,
                url=best.url or url,
                raw_text=best.text[:200],
            )
        ]


class ToretokuSource(KaitoriListSource):
    """トレトク 買取価格表（ポケカ／ワンピース）。div.item > p.item__name / p.item__price span.price"""

    name = "toretoku"

    def parse(self, html: str, url: str) -> list[Listing]:
        return parse_toretoku(html)


class PriceBaseSource(KaitoriListSource):
    """PRICE BASE 買取表（ポケカ／ワンピース／フュージョンワールド）。ul.price-list li"""

    name = "pricebase"

    def parse(self, html: str, url: str) -> list[Listing]:
        return parse_pricebase(html)


def parse_toretoku(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[Listing] = []
    for item in soup.select("div.item"):
        name_el = item.select_one("p.item__name")
        price_el = item.select_one("p.item__price span.price") or item.select_one("span.price")
        if not name_el or not price_el:
            continue
        price = parse_price(price_el.get_text(" ", strip=True))
        if price is None:
            continue
        listings.append(Listing(text=name_el.get_text(" ", strip=True), price=price))
    return listings


def parse_pricebase(html: str) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[Listing] = []
    for li in soup.select("ul.price-list li"):
        price_el = li.select_one("p.price-list-price")
        if not price_el:
            continue
        price = parse_price(price_el.get_text(" ", strip=True))
        if price is None:
            continue
        parts = [
            el.get_text(" ", strip=True)
            for el in (li.select_one("p.price-list-title"), li.select_one("p.price-list-meta"))
            if el
        ]
        text = " ".join(parts) if parts else li.get_text(" ", strip=True)
        listings.append(Listing(text=text, price=price))
    return listings
