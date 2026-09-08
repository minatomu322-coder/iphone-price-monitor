from __future__ import annotations

import re
from typing import Any

import requests
from bs4 import BeautifulSoup

from ..models import Observation, Product
from .base import parse_price, polite_sleep, timeouts


SELL_LABELS = ["販売価格", "価格"]
KAITORI_LABELS = ["買取価格", "買取上限", "買取金額"]
OUT_OF_STOCK_WORDS = ["品切れ", "在庫なし", "売り切れ", "販売終了"]
STOCK_RE = re.compile(r"在庫[:：]?\s*(\d+)")


class SurugayaSource:
    """駿河屋。販売ページ（buy側）と買取ページ（sell側 kaitori）の両方を扱う。

    ページ構造は変わり得るため、マークアップに依存せず「ラベル文字列の近くにある価格」を拾う。
    初回導入時は `python -m tcg.inspect <URL>` で抽出結果を目視確認すること。
    """

    name = "surugaya"

    def __init__(self, session: requests.Session, settings: dict[str, Any], scraping: dict[str, Any]) -> None:
        self.session = session
        self.settings = settings
        self.scraping = scraping

    def _get(self, url: str) -> str:
        polite_sleep(float(self.settings.get("request_delay_seconds", 3)))
        response = self.session.get(url, timeout=timeouts(self.scraping))
        response.raise_for_status()
        response.encoding = response.apparent_encoding or response.encoding
        return response.text

    def fetch(self, product: Product) -> list[Observation]:
        observations: list[Observation] = []
        if product.surugaya_url:
            html = self._get(product.surugaya_url)
            if "search" in product.surugaya_url:
                observations.extend(parse_search_page(html, product, product.surugaya_url, self.settings))
            else:
                obs = parse_product_page(html, product, product.surugaya_url)
                if obs:
                    observations.append(obs)
        if product.surugaya_kaitori_url:
            html = self._get(product.surugaya_kaitori_url)
            obs = parse_kaitori_page(html, product, product.surugaya_kaitori_url)
            if obs:
                observations.append(obs)
        return observations


def _flatten(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(" ", strip=True)


def labeled_price(text: str, labels: list[str], window: int = 40) -> int | None:
    """ラベル文字列の直後 window 文字以内にある最初の価格を返す。"""
    for label in labels:
        for match in re.finditer(re.escape(label), text):
            snippet = text[match.end(): match.end() + window]
            price = parse_price(snippet)
            if price:
                return price
    return None


def detect_stock(text: str) -> int | None:
    if any(word in text for word in OUT_OF_STOCK_WORDS):
        return 0
    match = STOCK_RE.search(text)
    return int(match.group(1)) if match else None


def parse_product_page(html: str, product: Product, url: str) -> Observation | None:
    text = _flatten(html)
    price = labeled_price(text, SELL_LABELS)
    if price is None:
        return None
    return Observation(
        product_id=product.product_id,
        source="surugaya",
        side="buy",
        channel="surugaya",
        price=price,
        url=url,
        stock=detect_stock(text),
        raw_text=text[:200],
    )


def parse_kaitori_page(html: str, product: Product, url: str) -> Observation | None:
    text = _flatten(html)
    price = labeled_price(text, KAITORI_LABELS)
    if price is None:
        return None
    return Observation(
        product_id=product.product_id,
        source="surugaya",
        side="sell",
        channel="kaitori",
        price=price,
        url=url,
        raw_text=text[:200],
    )


def parse_search_page(html: str, product: Product, url: str, settings: dict[str, Any]) -> list[Observation]:
    """検索結果ページから、商品名が合致する行の価格を拾う（セレクタは config で調整）。"""
    soup = BeautifulSoup(html, "html.parser")
    item_selector = settings.get("search_item_selector", ".item")
    title_selector = settings.get("search_title_selector", ".title")
    price_selector = settings.get("search_price_selector", ".price")
    found: list[Observation] = []
    for block in soup.select(item_selector):
        title_el = block.select_one(title_selector)
        title = title_el.get_text(" ", strip=True) if title_el else block.get_text(" ", strip=True)
        if not product.matches(title):
            continue
        price_el = block.select_one(price_selector)
        price = parse_price(price_el.get_text(" ", strip=True)) if price_el else None
        if price is None:
            continue
        link = block.find("a", href=True)
        href = link["href"] if link else url
        if href.startswith("/"):
            href = "https://www.suruga-ya.jp" + href
        block_text = block.get_text(" ", strip=True)
        found.append(
            Observation(
                product_id=product.product_id,
                source="surugaya",
                side="buy",
                channel="surugaya",
                price=price,
                url=href,
                stock=detect_stock(block_text),
                raw_text=title[:200],
            )
        )
    found.sort(key=lambda obs: obs.price)
    return found[:5]
