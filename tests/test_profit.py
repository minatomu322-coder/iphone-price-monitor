from __future__ import annotations

from rakuten_finder.config import Assumptions
from rakuten_finder.profit import calc_point_total, calc_profit


def test_calc_point_total_basic():
    # 30,000円 × (1 + 5)% = 1,800pt
    asm = Assumptions(spu_rate=5.0, campaign_rate=0.0, point_cap=0)
    assert calc_point_total(30000, 1.0, asm) == 1800


def test_calc_point_total_with_campaign_and_deal():
    # 30,000円 × (1 + 5 + 9 + 20)% = 10,500pt（買い回り9倍 + DEAL20%）
    asm = Assumptions(spu_rate=5.0, campaign_rate=9.0, point_cap=0)
    assert calc_point_total(30000, 1.0, asm, super_deal_rate=20.0) == 10500


def test_calc_point_total_cap():
    asm = Assumptions(spu_rate=5.0, campaign_rate=0.0, point_cap=1000)
    assert calc_point_total(30000, 1.0, asm) == 1000


def test_calc_profit(item, stats, assumptions):
    result = calc_profit(item, stats, assumptions)
    # ポイント: 30,000 × 6% = 1,800 → 実質仕入 28,200
    assert result.point_total == 1800
    assert result.effective_cost == 30000 - 1800
    # 売価 36,500 - 手数料 3,650 - 送料 200 - 実質 28,200 = 4,450
    assert result.sell_price == 36500
    assert result.mercari_fee == 3650
    assert result.profit == 4450
    assert result.roi > 0.15
    assert 0 < result.margin < 1


def test_calc_profit_shipping_not_included(item, stats, assumptions):
    import dataclasses

    item2 = dataclasses.replace(item, shipping_included=False)
    asm2 = dataclasses.replace(assumptions, default_shipping_in=500)
    result = calc_profit(item2, stats, asm2)
    assert result.shipping_in == 500
    assert result.effective_cost == 30000 + 500 - 1800


def test_calc_profit_with_coupon(item, stats, assumptions):
    result = calc_profit(item, stats, assumptions, coupon=2000)
    assert result.effective_cost == 30000 - 2000 - 1800


def test_calc_profit_sell_min_mode(item, stats, assumptions):
    import dataclasses

    asm = dataclasses.replace(assumptions, sell_percentile="min")
    result = calc_profit(item, stats, asm)
    assert result.sell_price == 34000  # sold_min を使う（保守的モード）
