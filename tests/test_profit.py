from __future__ import annotations

from tcg.profit import compute_profit, comparison_text, flea_limit_price, shipping_cost


def test_best_channel_is_highest_net(config, single):
    result = compute_profit(single, 10000, {"mercari": 15000, "kaitori": 12000}, config)
    assert result is not None
    names = {r.channel for r in result.channels}
    assert names == {"mercari", "kaitori", "wholesale"}
    mercari = next(r for r in result.channels if r.channel == "mercari")
    assert mercari.fee == 1500
    assert mercari.shipping == 210 and mercari.packing == 30
    assert mercari.net == 15000 - 1500 - 240
    assert mercari.profit == mercari.net - 10000
    # 卸は買取上限×1.05、手数料ゼロ
    wholesale = next(r for r in result.channels if r.channel == "wholesale")
    assert wholesale.gross == 12600
    assert result.best.channel == "mercari"


def test_kaitori_wins_when_flea_price_missing(config, single):
    result = compute_profit(single, 10000, {"kaitori": 13000}, config)
    assert result is not None
    assert result.best.channel == "wholesale"   # 13000×1.05 > 13000
    assert all(r.channel != "mercari" for r in result.channels)


def test_no_sell_price_returns_none(config, single):
    assert compute_profit(single, 10000, {}, config) is None
    assert compute_profit(single, 0, {"mercari": 1}, config) is None


def test_high_value_uses_insured_shipping(config, single):
    assert shipping_cost(single, 10000, config) == 210
    assert shipping_cost(single, 150000, config) == 1200


def test_box_shipping(config, box):
    assert shipping_cost(box, 20000, config) == 800


def test_flea_limit_price_rounds_down_to_100(config):
    # net 13,260 / 1.15 = 11,530 → 11,500
    assert flea_limit_price(13260, config) == 11500
    assert flea_limit_price(0, config) is None


def test_comparison_text(config, single):
    result = compute_profit(single, 10000, {"mercari": 15000, "kaitori": 12000}, config)
    text = comparison_text(result)
    assert "メルカリ" in text and "買取店" in text and "｜" in text
