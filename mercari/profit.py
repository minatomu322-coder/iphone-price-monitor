from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


DEFAULT_FEE_RATE = 0.10  # メルカリ販売手数料10%


@dataclass(frozen=True)
class ProfitEstimate:
    price: int          # 販売価格
    fee: int            # 販売手数料
    sell_shipping: int  # 販売送料
    cost_total: int     # 仕入れ価格+仕入れ送料+その他
    profit: int         # 手取り利益
    roi: float | None   # profit / cost_total（原価0のときはNone）


def sales_fee(price: int, fee_rate: float = DEFAULT_FEE_RATE) -> int:
    """販売手数料。端数は切り捨て（実際の請求と数円ずれる可能性はChatGPT側で確認）。"""
    return math.floor(price * fee_rate)


def estimate_profit(
    price: int,
    purchase_price: int,
    purchase_shipping: int = 0,
    sell_shipping: int = 0,
    other_cost: int = 0,
    fee_rate: float = DEFAULT_FEE_RATE,
) -> ProfitEstimate:
    fee = sales_fee(price, fee_rate)
    cost_total = int(purchase_price) + int(purchase_shipping) + int(other_cost)
    profit = int(price) - fee - int(sell_shipping) - cost_total
    roi = round(profit / cost_total, 3) if cost_total > 0 else None
    return ProfitEstimate(
        price=int(price),
        fee=fee,
        sell_shipping=int(sell_shipping),
        cost_total=cost_total,
        profit=profit,
        roi=roi,
    )


def round_price(price: float, round_to: int = 100) -> int:
    """販売価格を切りのよい値に丸める（下方向）。300円未満にはしない。"""
    rounded = int(price // round_to * round_to)
    return max(rounded, 300)


def price_ladder(base_price: int, pricing: dict[str, Any] | None = None) -> dict[str, int]:
    """相場（中央値など）から3段階の価格候補を作る。

    早売り（quick）/ 相場（standard）/ 強気（strong）。
    最終的な採用判断はChatGPTとユーザーが行う。
    """
    pricing = pricing or {}
    round_to = int(pricing.get("round_to", 100))
    return {
        "quick": round_price(base_price * float(pricing.get("quick_ratio", 0.90)), round_to),
        "standard": round_price(base_price * float(pricing.get("standard_ratio", 1.00)), round_to),
        "strong": round_price(base_price * float(pricing.get("strong_ratio", 1.08)), round_to),
    }


def breakeven_price(
    purchase_price: int,
    purchase_shipping: int = 0,
    sell_shipping: int = 0,
    other_cost: int = 0,
    fee_rate: float = DEFAULT_FEE_RATE,
) -> int:
    """利益0になる販売価格（これ未満は赤字）。"""
    cost = int(purchase_price) + int(purchase_shipping) + int(sell_shipping) + int(other_cost)
    return math.ceil(cost / (1 - fee_rate))
