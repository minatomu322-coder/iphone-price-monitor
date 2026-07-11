from __future__ import annotations

from typing import Any

from mercari.profit import DEFAULT_FEE_RATE, estimate_profit, price_ladder


# 一次判定はあくまでシステムの機械的なスクリーニング。
# 最終判断（買い/条件付きで買い/見送り/追加確認）はChatGPTとユーザーが行う。
LABEL_BUY = "買い候補"
LABEL_CONDITIONAL = "条件付き候補"
LABEL_SKIP = "見送り候補"
LABEL_NEED_INFO = "追加確認"


def primary_judgement(
    item: dict[str, Any],
    market: dict[str, Any] | None,
    market_history: list[dict[str, Any]] | None,
    config: dict[str, Any],
) -> dict[str, Any]:
    """仕入れ候補の一次判定。ラベル・理由・価格3案・利益試算を返す。"""
    thresholds = config.get("thresholds", {})
    pricing = config.get("pricing", {})
    fee_rate = float(config.get("fees", {}).get("default_rate", DEFAULT_FEE_RATE))
    min_profit = int(thresholds.get("min_profit", 1000))
    min_roi = float(thresholds.get("min_roi", 0.15))
    min_sold = int(thresholds.get("min_sold_count", 3))
    max_active_ratio = float(thresholds.get("max_active_ratio", 3.0))

    purchase_price = item.get("purchase_price")
    reasons: list[str] = []
    warnings: list[str] = []

    if purchase_price is None:
        return {
            "label": LABEL_NEED_INFO,
            "reasons": ["仕入れ価格が未入力"],
            "warnings": [],
            "ladder": None,
        }
    if not market or market.get("median_price") is None:
        return {
            "label": LABEL_NEED_INFO,
            "reasons": ["相場データ（売り切れ中央値）が未登録"],
            "warnings": [],
            "ladder": None,
        }

    sold_count = market.get("sold_count")
    active_count = market.get("active_count")
    if sold_count is not None and sold_count < min_sold:
        warnings.append(f"売り切れ件数が{sold_count}件と少なく相場の信頼性が低い")
    if sold_count and active_count and active_count / max(sold_count, 1) >= max_active_ratio:
        warnings.append(
            f"販売中{active_count}件/売り切れ{sold_count}件で供給過多。回転が悪い可能性"
        )
    if market_history and len(market_history) >= 2:
        first = market_history[0].get("median_price")
        last = market_history[-1].get("median_price")
        if first and last and last < first:
            warnings.append(f"相場中央値が下落傾向（{first:,}円 → {last:,}円）")

    median = int(market["median_price"])
    ladder_prices = price_ladder(median, pricing)
    ladder = {}
    for key, price in ladder_prices.items():
        est = estimate_profit(
            price,
            purchase_price=int(purchase_price),
            purchase_shipping=int(item.get("purchase_shipping") or 0),
            sell_shipping=int(item.get("shipping_cost") or 0),
            fee_rate=fee_rate,
        )
        ladder[key] = {
            "price": est.price,
            "fee": est.fee,
            "profit": est.profit,
            "roi": est.roi,
        }

    standard = ladder["standard"]
    quick = ladder["quick"]

    if sold_count is not None and sold_count < min_sold:
        label = LABEL_NEED_INFO
        reasons.append("相場件数が不足しているため追加確認が必要")
    elif standard["profit"] >= min_profit and (standard["roi"] or 0) >= min_roi:
        label = LABEL_BUY
        reasons.append(
            f"相場価格{standard['price']:,}円で利益{standard['profit']:+,}円"
            f"（ROI {round((standard['roi'] or 0) * 100)}%）"
        )
        if quick["profit"] >= 0:
            reasons.append(f"早売り価格{quick['price']:,}円でも利益{quick['profit']:+,}円を確保")
    elif standard["profit"] > 0:
        label = LABEL_CONDITIONAL
        reasons.append(
            f"相場価格で利益{standard['profit']:+,}円と薄利。"
            f"基準（利益{min_profit:,}円以上・ROI {round(min_roi * 100)}%以上）未満"
        )
    else:
        label = LABEL_SKIP
        reasons.append(f"相場価格{standard['price']:,}円では利益{standard['profit']:+,}円で赤字")

    return {"label": label, "reasons": reasons, "warnings": warnings, "ladder": ladder}


def is_stale(listing: dict[str, Any], days_elapsed: int | None, config: dict[str, Any]) -> bool:
    """出品後、規定日数を超えても売れていないものを売れ残りとみなす。"""
    stale_days = int(config.get("thresholds", {}).get("stale_days", 14))
    return (
        listing.get("status") == "active"
        and days_elapsed is not None
        and days_elapsed >= stale_days
    )
