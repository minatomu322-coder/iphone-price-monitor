from __future__ import annotations

from datetime import date
from typing import Any

from mercari.db import MercariDatabase, today_jst


AGING_BUCKETS = (
    ("0〜30日", 0, 30),
    ("31〜60日", 31, 60),
    ("61日以上", 61, None),
)


def days_in_stock(item: dict[str, Any], today: str | None = None) -> int | None:
    """仕入れ日から今日までの在庫日数（仕入れ日未入力ならNone）。"""
    purchased_at = item.get("purchased_at")
    if not purchased_at:
        return None
    try:
        start = date.fromisoformat(str(purchased_at)[:10])
    except ValueError:
        return None
    end = date.fromisoformat((today or today_jst())[:10])
    return (end - start).days


def item_capital(item: dict[str, Any]) -> int:
    """その商品に寝ている資金（仕入れ価格＋仕入れ送料）。"""
    return int(item.get("purchase_price") or 0) + int(item.get("purchase_shipping") or 0)


def inventory_aging(
    stock_items: list[dict[str, Any]], today: str | None = None
) -> list[dict[str, Any]]:
    """在庫年齢バケットごとの件数と寝ている資金。仕入れ日未入力は「日数不明」に入れる。"""
    buckets = [
        {"label": label, "count": 0, "capital": 0, "min_days": lo, "max_days": hi}
        for label, lo, hi in AGING_BUCKETS
    ]
    unknown = {"label": "日数不明", "count": 0, "capital": 0, "min_days": None, "max_days": None}
    for item in stock_items:
        days = days_in_stock(item, today)
        capital = item_capital(item)
        if days is None:
            unknown["count"] += 1
            unknown["capital"] += capital
            continue
        for bucket in buckets:
            hi = bucket["max_days"]
            if days >= bucket["min_days"] and (hi is None or days <= hi):
                bucket["count"] += 1
                bucket["capital"] += capital
                break
    if unknown["count"]:
        buckets.append(unknown)
    return buckets


def stock_summary(db: MercariDatabase, today: str | None = None) -> dict[str, Any]:
    """在庫（仕入れ済み・出品中）の資金サマリー。"""
    stock = [i for i in db.list_items() if i["status"] in ("purchased", "listed")]
    return {
        "count": len(stock),
        "capital": sum(item_capital(i) for i in stock),
        "aging": inventory_aging(stock, today),
        "items": stock,
    }
