from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from ..models import Observation, Product
from .base import parse_price, polite_sleep, timeouts
from .listing import Listing, condition_excluded, condition_of, debug_enabled, matches_product, stock_of


# カードラッシュ通販（タイトルごとに別ドメイン）。robots.txt は一般UAを許可。
DEFAULT_HOSTS = {
    "pokemon": "https://www.cardrush-pokemon.jp",
    "onepiece": "https://www.cardrush-op.jp",
    "fusionworld": "https://www.cardrush-db.jp",
}
# 出口価格は美品前提なので、仕入側も 状態A- 以下は既定で除外（config で緩められる）
DEFAULT_EXCLUDED_CONDITIONS = ["PSA", "鑑定", "状態A-", "状態B", "状態C", "状態D", "キズ", "傷"]
# 検索語から落とす語（検索に効かず、むしろヒットを減らす）
KEYWORD_NOISE = re.compile(r"リーダー|SEC|SR|SAR|SCR|プロモ|P-\d+|[（(].*?[)）]|☆+|★+")


class CardrushSource:
    """カードラッシュの販売価格（buy側）。検索結果ページから、型番が一致する行の最安を拾う。"""

    name = "cardrush"

    def __init__(self, session: requests.Session, settings: dict[str, Any], scraping: dict[str, Any]) -> None:
        self.session = session
        self.settings = settings
        self.scraping = scraping
        self.hosts = {**DEFAULT_HOSTS, **(settings.get("hosts") or {})}

    def keywords(self, product: Product) -> list[str]:
        """検索語の候補。まず商品名からノイズ語を除いたもの、0件なら先頭の語（キャラ名）だけで再検索。"""
        primary = re.sub(r"\s+", " ", KEYWORD_NOISE.sub(" ", product.name)).strip() or product.name
        fallback = primary.split(" ")[0]
        return [primary] if fallback == primary else [primary, fallback]

    def search_url(self, product: Product, keyword: str) -> str | None:
        host = self.hosts.get(product.title)
        if not host:
            return None
        return f"{host}/product-list?keyword={quote(keyword)}"

    def fetch(self, product: Product) -> list[Observation]:
        listings: list[Listing] = []
        url = ""
        for keyword in self.keywords(product):
            url = self.search_url(product, keyword) or ""
            if not url:
                return []
            polite_sleep(float(self.settings.get("request_delay_seconds", 3)))
            response = self.session.get(url, timeout=timeouts(self.scraping))
            response.raise_for_status()
            listings = parse_search_results(response.text, url)
            if listings:
                break
        observations = to_observations(product, listings, self.settings)
        if debug_enabled():
            print(f"[debug] cardrush {product.product_id}: 行 {len(listings)} → 一致 {len(observations)} {url}")
            for obs in observations[:3]:
                print(f"[debug]   {obs.price:,}円 stock={obs.stock} {obs.condition or '-'} {obs.raw_text[:70]}")
            if listings and not observations:
                for item in listings[:3]:
                    print(f"[debug]   不一致例: {item.price:,}円 {item.text[:80]}")
        return observations


def parse_search_results(html: str, base_url: str) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[Listing] = []
    for cell in soup.select("li.list_item_cell"):
        data = cell.select_one("div.item_data") or cell
        text = data.get_text(" ", strip=True)
        price_el = cell.select_one("p.selling_price span.figure") or cell.select_one("span.figure")
        price = parse_price(price_el.get_text(" ", strip=True)) if price_el else None
        if price is None:
            continue
        link = cell.select_one("a.item_data_link[href]") or cell.find("a", href=True)
        href = link["href"] if link else base_url
        if href.startswith("/"):
            href = base_url.split("/product-list")[0] + href
        listings.append(
            Listing(text=text, price=price, url=href, stock=stock_of(text), condition=condition_of(text))
        )
    return listings


def to_observations(product: Product, listings: list[Listing], settings: dict[str, Any]) -> list[Observation]:
    excluded = list(settings.get("exclude_conditions") or DEFAULT_EXCLUDED_CONDITIONS)
    max_listings = int(settings.get("max_listings", 5))
    found: list[Observation] = []
    for item in listings:
        if not matches_product(product, item.text):
            continue
        if item.condition and condition_excluded(item.condition, excluded):
            continue
        if item.stock is not None and item.stock <= 0:
            continue
        found.append(
            Observation(
                product_id=product.product_id,
                source="cardrush",
                side="buy",
                channel="cardrush",
                price=item.price,
                url=item.url,
                condition=item.condition,
                stock=item.stock,
                raw_text=item.text[:200],
            )
        )
    found.sort(key=lambda obs: obs.price)
    return found[:max_listings]
