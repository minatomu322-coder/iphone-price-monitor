from __future__ import annotations

import statistics
from datetime import date, timedelta
from typing import Any

from .database import TcgDatabase
from .models import Metrics, Product


def compute_metrics(db: TcgDatabase, product: Product, as_of: date, config: dict[str, Any]) -> Metrics:
    """DBの観測から、当日の指標を1商品分まとめる。"""
    metrics = Metrics(product_id=product.product_id, as_of=as_of)

    # --- buy側：当日の最安仕入 ---
    buys = [row for row in db.observations_on(product.product_id, as_of) if row["side"] == "buy"]
    metrics.listing_count = len(buys)
    if buys:
        best = min(buys, key=lambda row: int(row["price"]))
        metrics.buy_min = int(best["price"])
        metrics.buy_source = str(best["source"])
        metrics.buy_url = str(best["url"])
        metrics.buy_condition = str(best.get("condition") or "")
        stocks = [int(row["stock"]) for row in buys if row.get("stock") is not None]
        metrics.stock_total = sum(stocks) if stocks else None

    # --- sell側：チャネルごとに直近の観測（手動シートは毎日更新されない） ---
    max_age = int(config.get("sell_price_max_age_days", 21))
    for row in db.latest_sell_observations(product.product_id, max_age, as_of):
        channel = str(row["channel"])
        price = int(row["price"])
        metrics.sell_prices[channel] = max(metrics.sell_prices.get(channel, 0), price)
        if row.get("sold_count_30d") is not None:
            sold = int(row["sold_count_30d"])
            metrics.sold_count_30d = max(metrics.sold_count_30d or 0, sold)

    # --- トレンド：日次の最安仕入価格の系列 ---
    series = db.daily_buy_min_series(product.product_id, 90, as_of)
    metrics.chg_7d = change_since(series, as_of, 7)
    metrics.chg_30d = change_since(series, as_of, 30)
    metrics.chg_90d = change_since(series, as_of, 90)
    metrics.volatility = volatility(series, as_of, 30)
    metrics.history_days = db.history_days(product.product_id)
    metrics.liquidity = liquidity_score(metrics.sold_count_30d, metrics.listing_count)
    return metrics


def change_since(series: list[tuple[date, int]], as_of: date, days: int) -> float | None:
    """N日前（その日以前で最も近い観測）と当日の騰落率。データ不足なら None。"""
    if not series:
        return None
    today = next((price for day, price in series if day == as_of), None)
    if today is None:
        return None
    target = as_of - timedelta(days=days)
    tolerance = timedelta(days=max(2, days // 3))
    past = [(day, price) for day, price in series if day <= target and day >= target - tolerance]
    if not past:
        return None
    _, base = past[-1]
    if base <= 0:
        return None
    return round((today - base) / base, 4)


def volatility(series: list[tuple[date, int]], as_of: date, days: int) -> float | None:
    """直近N日の 標準偏差 / 平均。5点未満なら None。"""
    since = as_of - timedelta(days=days)
    prices = [price for day, price in series if day >= since]
    if len(prices) < 5:
        return None
    mean = statistics.fmean(prices)
    if mean <= 0:
        return None
    return round(statistics.pstdev(prices) / mean, 4)


def liquidity_score(sold_count_30d: int | None, listing_count: int) -> float:
    """0..1 の流動性。フリマの直近売れ数があればそれを優先し、無ければ出品数で弱く代替。"""
    if sold_count_30d is not None:
        return round(min(1.0, sold_count_30d / 30.0), 3)
    return round(min(1.0, listing_count / 10.0) * 0.5, 3)
