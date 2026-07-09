"""楽天市場商品検索API（IchibaItem/Search/20220601）クライアント。

公式 API を使うため、スクレイピングに比べて壊れにくく規約面でも安全。
アプリID は https://webservice.rakuten.co.jp/ で無料発行できる。

MVP では楽天市場のみ。将来 楽天ブックス（BooksTotal/Search）や
楽天24（＝市場のショップ絞り込み）を同じ形式で追加できるよう、
検索パラメータは Target 単位で受け取る。
"""
from __future__ import annotations

import time
from typing import Any

import requests

from .config import Config, Target
from .models import RakutenItem

ICHIBA_SEARCH_URL = (
    "https://app.rakuten.co.jp/services/api/IchibaItem/Search/20220601"
)


class RakutenApiError(RuntimeError):
    """楽天API 呼び出しの失敗。"""


def search_items(config: Config, target: Target) -> list[RakutenItem]:
    """キーワード検索して RakutenItem のリストを返す。"""
    app_id = config.app_id
    if not app_id:
        raise RakutenApiError(
            f"楽天アプリIDが未設定です。環境変数 {config.app_id_env} を設定してください。"
        )

    params: dict[str, Any] = {
        "applicationId": app_id,
        "keyword": target.keyword,
        "hits": min(int(target.hits or config.hits_per_keyword), 30),  # API上限30
        "sort": "+itemPrice",
        "availability": 1,        # 在庫ありのみ
        "formatVersion": 2,
    }
    if config.affiliate_id:
        params["affiliateId"] = config.affiliate_id
    if target.genre_id:
        params["genreId"] = target.genre_id
    if target.min_price:
        params["minPrice"] = int(target.min_price)
    if target.max_price:
        params["maxPrice"] = int(target.max_price)

    data = _request(params, config.request_delay_seconds)
    items: list[RakutenItem] = []
    for entry in data.get("Items", []):
        # formatVersion=2 はフラット、1 は {"Item": {...}} 形式。両対応。
        raw = entry.get("Item", entry) if isinstance(entry, dict) else entry
        item = parse_item(raw, target.keyword)
        if item is None:
            continue
        if _matches_ng(item.name, target.ng_keywords):
            continue
        items.append(item)
    return items


def _request(params: dict[str, Any], delay_seconds: float) -> dict[str, Any]:
    if delay_seconds > 0:
        time.sleep(delay_seconds)  # ポライトアクセス（API レート制限 1req/s 対策）
    try:
        response = requests.get(ICHIBA_SEARCH_URL, params=params, timeout=(10, 30))
    except requests.RequestException as exc:
        raise RakutenApiError(f"楽天APIへの接続に失敗: {type(exc).__name__}: {exc}") from exc
    if response.status_code != 200:
        # 429/5xx は呼び出し側でリトライ判断できるよう本文の要約を残す
        raise RakutenApiError(
            f"楽天APIエラー HTTP {response.status_code}: {response.text[:300]}"
        )
    return response.json()


def parse_item(raw: dict[str, Any], keyword: str) -> RakutenItem | None:
    """API レスポンス 1 件を RakutenItem に変換。壊れたデータは None。"""
    try:
        name = str(raw["itemName"])
        price = int(raw["itemPrice"])
        item_code = str(raw.get("itemCode") or raw["itemUrl"])
    except (KeyError, TypeError, ValueError):
        return None

    image_urls = raw.get("mediumImageUrls") or raw.get("smallImageUrls") or []
    image_url: str | None = None
    if image_urls:
        first = image_urls[0]
        image_url = first.get("imageUrl") if isinstance(first, dict) else str(first)

    catchcopy = str(raw.get("catchcopy") or "")
    return RakutenItem(
        item_code=item_code,
        name=name,
        price=price,
        url=str(raw.get("affiliateUrl") or raw.get("itemUrl") or ""),
        shop_name=str(raw.get("shopName") or ""),
        keyword=keyword,
        shipping_included=int(raw.get("postageFlag", 1)) == 0,
        point_rate=float(raw.get("pointRate", 1)),
        in_stock=int(raw.get("availability", 1)) == 1,
        image_url=image_url,
        genre_id=str(raw.get("genreId") or "") or None,
        jan=RakutenItem.extract_jan(name, catchcopy),
        catchcopy=catchcopy,
        review_count=int(raw.get("reviewCount", 0) or 0),
        review_average=float(raw.get("reviewAverage", 0) or 0),
    )


def _matches_ng(name: str, ng_keywords: list[str]) -> bool:
    lower = name.lower()
    return any(str(ng).lower() in lower for ng in ng_keywords if ng)
