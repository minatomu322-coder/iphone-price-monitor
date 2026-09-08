from __future__ import annotations

import math
from typing import Any

from .models import ChannelResult, Product, ProfitResult


def shipping_cost(product: Product, buy_price: int, config: dict[str, Any]) -> int:
    fees = config.get("fees", {})
    table = fees.get("shipping", {})
    base = int(table.get(product.form, table.get("single", 210)))
    threshold = int(config.get("risk", {}).get("high_value_threshold", 100000))
    if buy_price >= threshold:
        base = max(base, int(table.get("high_value", base)))
    return base


def compute_profit(
    product: Product,
    buy_price: int,
    sell_prices: dict[str, int],
    config: dict[str, Any],
) -> ProfitResult | None:
    """4チャネルの手取りを並べて、最も手取りが高いチャネルを推奨出口にする。"""
    if buy_price <= 0:
        return None
    channels_cfg: dict[str, dict[str, Any]] = config.get("channels", {})
    packing = int(config.get("fees", {}).get("packing", 30))
    shipping = shipping_cost(product, buy_price, config)
    results: list[ChannelResult] = []
    for channel, settings in channels_cfg.items():
        if not settings.get("enabled", True):
            continue
        gross = channel_gross(channel, settings, sell_prices)
        if gross is None:
            continue
        fee = int(round(gross * float(settings.get("fee_rate", 0.0))))
        net = gross - fee - shipping - packing
        profit = net - buy_price
        results.append(
            ChannelResult(
                channel=channel,
                gross=gross,
                fee=fee,
                shipping=shipping,
                packing=packing,
                net=net,
                profit=profit,
                margin=round(profit / buy_price, 4),
                turnover_days=int(settings.get("turnover_days", 14)),
            )
        )
    if not results:
        return None
    results.sort(key=lambda r: (r.profit, -r.turnover_days), reverse=True)
    best = results[0]
    return ProfitResult(
        buy_price=buy_price,
        channels=results,
        best=best,
        flea_limit_price=flea_limit_price(best.net, config),
    )


def channel_gross(channel: str, settings: dict[str, Any], sell_prices: dict[str, int]) -> int | None:
    """チャネルの想定売価。業者間卸は買取上限×掛け率で近似。"""
    if channel == "wholesale":
        kaitori = sell_prices.get("kaitori")
        if kaitori is None:
            return None
        return int(round(kaitori * float(settings.get("rate_of_kaitori", 1.0))))
    price = sell_prices.get(channel)
    return int(price) if price else None


def flea_limit_price(best_net: int, config: dict[str, Any]) -> int | None:
    """「この仕入価格以下なら短期の目標利益率を確保できる」指値。100円単位に切り下げ。"""
    flea = config.get("flea_market", {})
    if not flea.get("emit_limit_price", True):
        return None
    target_margin = float(config.get("strategies", {}).get("short", {}).get("min_margin", 0.15))
    unit = int(flea.get("limit_price_round", 100))
    limit = best_net / (1.0 + target_margin)
    if limit <= 0:
        return None
    return int(math.floor(limit / unit) * unit)


def comparison_text(result: ProfitResult) -> str:
    """CSVの「チャネル比較」列。例: メルカリ+3,200/14日 ｜ 買取店+1,800/1日"""
    parts = [f"{r.label}{r.profit:+,}/{r.turnover_days}日" for r in result.channels]
    return " ｜ ".join(parts)
