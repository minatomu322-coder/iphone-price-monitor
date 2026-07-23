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


def _month_range(anchor: str, offset: int) -> tuple[str, str, str]:
    """anchor(YYYY-MM-DD)からoffsetヶ月前の (ラベル, 月初, 月末) を返す。"""
    year, month = int(anchor[:4]), int(anchor[5:7])
    month -= offset
    while month <= 0:
        month += 12
        year -= 1
    label = f"{year}-{month:02d}"
    if month == 12:
        next_year, next_month = year + 1, 1
    else:
        next_year, next_month = year, month + 1
    last_day = (date(next_year, next_month, 1) - date.resolution).isoformat()
    return label, f"{label}-01", last_day


def _sale_profit(sale: dict[str, Any]) -> int:
    cost = int(sale.get("purchase_price") or 0) + int(sale.get("purchase_shipping") or 0)
    return (
        int(sale["sold_price"]) - int(sale["sales_fee"])
        - int(sale["shipping_cost"]) - int(sale["other_cost"]) - cost
    )


def monthly_kpis(
    db: MercariDatabase, months: int = 6, today: str | None = None
) -> list[dict[str, Any]]:
    """直近Nヶ月の月次KPI（古い月→新しい月の順）。"""
    anchor = today or today_jst()
    series: list[dict[str, Any]] = []
    for offset in range(months - 1, -1, -1):
        label, month_start, month_end = _month_range(anchor, offset)
        sales = db.sales_between(month_start, month_end)
        profits = [_sale_profit(s) for s in sales]
        revenue = sum(s["sold_price"] for s in sales)
        costs = sum(
            int(s.get("purchase_price") or 0) + int(s.get("purchase_shipping") or 0)
            for s in sales
        )
        turn_days = [s["days_to_sell"] for s in sales if s.get("days_to_sell") is not None]
        series.append({
            "month": label,
            "count": len(sales),
            "revenue": revenue,
            "profit": sum(profits),
            "margin": round(sum(profits) / revenue, 3) if revenue else None,
            "roi": round(sum(profits) / costs, 3) if costs else None,
            "avg_days_to_sell": round(sum(turn_days) / len(turn_days), 1) if turn_days else None,
            "loss_count": sum(1 for p in profits if p < 0),
        })
    return series


def failure_costs(
    db: MercariDatabase,
    date_from: str = "0000-01-01",
    date_to: str = "9999-12-31",
) -> dict[str, Any]:
    """失敗コストの見える化。利益の総額だけでなく「いくら失ったか」を出す。

    - 累計利益: 黒字売却の利益合計
    - 累計赤字: 赤字売却の損失合計（負の値）
    - 純利益: 上記の合計
    - 機会損失（値下げ）: 出品時価格より安く売った差額の合計
    - 見送り誤り: 「見送り」判断の後に相場が上昇したと答え合わせされた件数
    - 失敗率: 赤字売却 ÷ 全売却
    """
    sales = db.sales_between(date_from, date_to)
    gross_profit = 0
    gross_loss = 0
    markdown_loss = 0
    loss_count = 0
    revenue = 0
    for s in sales:
        profit = _sale_profit(s)
        revenue += int(s["sold_price"])
        if profit >= 0:
            gross_profit += profit
        else:
            gross_loss += profit
            loss_count += 1
        if s.get("listing_id"):
            listing = db.get_listing(int(s["listing_id"]))
            if listing and listing.get("list_price"):
                markdown_loss += max(0, int(listing["list_price"]) - int(s["sold_price"]))
    skip_errors = [
        r for r in db.all_reviews()
        if r.get("accuracy") == "incorrect" and "見送り" in (r.get("verdict") or "")
    ]
    net = gross_profit + gross_loss
    return {
        "sales_count": len(sales),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "net_profit": net,
        "markdown_loss": markdown_loss,
        "skip_error_count": len(skip_errors),
        "margin": round(net / revenue, 3) if revenue else None,
        "failure_rate": round(loss_count / len(sales), 3) if sales else None,
        "loss_count": loss_count,
    }


def kpi_dashboard(db: MercariDatabase, months: int = 6, today: str | None = None) -> dict[str, Any]:
    """KPIダッシュボード用の集計一式。"""
    stock = stock_summary(db, today)
    series = monthly_kpis(db, months, today)
    current = series[-1] if series else None
    # 資金効率の目安: 今月の利益 ÷ 現在在庫に寝ている資金（在庫ゼロならNone）
    capital_efficiency = None
    if current and stock["capital"]:
        capital_efficiency = round(current["profit"] / stock["capital"], 3)
    return {
        "months": series,
        "stock": {k: v for k, v in stock.items() if k != "items"},
        "capital_efficiency": capital_efficiency,
        "unsold_reasons": db.unsold_reason_stats(),
        "failure": failure_costs(db),
    }


def stock_summary(db: MercariDatabase, today: str | None = None) -> dict[str, Any]:
    """在庫（仕入れ済み・出品中）の資金サマリー。"""
    stock = [i for i in db.list_items() if i["status"] in ("purchased", "listed")]
    return {
        "count": len(stock),
        "capital": sum(item_capital(i) for i in stock),
        "aging": inventory_aging(stock, today),
        "items": stock,
    }
