"""利益計算。

実質仕入価格 = 楽天価格 + 仕入送料 - クーポン - 想定ポイント合計
想定利益     = メルカリ売価 - 手数料 - 発送送料 - 実質仕入価格

ポイント倍率は「商品ポイント（API の pointRate）+ SPU 想定 + キャンペーン想定
（買い回り / 5と0の日 / 勝ったら倍）+ スーパーDEAL 還元」を合算する。
獲得ポイントに上限がある場合は point_cap でクリップできる。
"""
from __future__ import annotations

from .config import Assumptions
from .models import MercariStats, ProfitResult, RakutenItem


def calc_point_total(
    price: int,
    item_point_rate: float,
    assumptions: Assumptions,
    super_deal_rate: float = 0.0,
) -> int:
    """想定獲得ポイント（円換算）。

    pointRate は「1」が通常（=1倍=1%）。SPU やキャンペーンはその上に載る。
    """
    total_rate = (
        max(item_point_rate, 1.0)
        + assumptions.spu_rate
        + assumptions.campaign_rate
        + super_deal_rate
    )
    points = int(price * total_rate / 100)
    if assumptions.point_cap > 0:
        points = min(points, assumptions.point_cap)
    return points


def calc_profit(
    item: RakutenItem,
    stats: MercariStats,
    assumptions: Assumptions,
    coupon: int = 0,
    super_deal_rate: float = 0.0,
) -> ProfitResult:
    """楽天商品 × メルカリ相場 から利益を計算する。"""
    shipping_in = 0 if item.shipping_included else assumptions.default_shipping_in
    point_total = calc_point_total(
        item.price, item.point_rate, assumptions, super_deal_rate
    )
    effective_cost = item.price + shipping_in - coupon - point_total

    sell_price = stats.sell_price(assumptions.sell_percentile)
    mercari_fee = round(sell_price * assumptions.mercari_fee_rate)
    profit = sell_price - mercari_fee - assumptions.shipping_out - effective_cost

    roi = profit / effective_cost if effective_cost > 0 else 0.0
    margin = profit / sell_price if sell_price > 0 else 0.0

    return ProfitResult(
        rakuten_price=item.price,
        shipping_in=shipping_in,
        coupon=coupon,
        point_total=point_total,
        effective_cost=effective_cost,
        sell_price=sell_price,
        mercari_fee=mercari_fee,
        shipping_out=assumptions.shipping_out,
        profit=profit,
        roi=round(roi, 4),
        margin=round(margin, 4),
    )
