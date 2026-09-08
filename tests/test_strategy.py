from __future__ import annotations

from datetime import date

from tcg.models import Product
from tcg.profit import compute_profit
from tcg.strategy import evaluate, risk_factor, select_daily
from tests.conftest import make_metrics


AS_OF = date(2026, 9, 8)


def candidates_for(product, config, buy, sell, **metrics_kw):
    m = make_metrics(product, buy_min=buy, buy_source="rakuten", sell_prices=sell, **metrics_kw)
    p = compute_profit(product, buy, sell, config)
    assert p is not None
    return evaluate(product, m, p, config, AS_OF)


def test_short_eligible_with_good_spread(config, single):
    cands = candidates_for(single, config, 10000, {"mercari": 15000, "kaitori": 11000}, liquidity=0.6, chg_7d=0.1, stock_total=2)
    strategies = {c.strategy for c in cands}
    assert "short" in strategies
    short = next(c for c in cands if c.strategy == "short")
    assert any("買取上限" in r for r in short.reasons)
    assert any("急騰" in r for r in short.reasons)
    assert any("14日以内" in r for r in short.risks)
    assert 0 < short.score <= 1


def test_short_rejected_when_margin_too_low(config, single):
    cands = candidates_for(single, config, 10000, {"mercari": 11500})
    assert all(c.strategy != "short" for c in cands)


def test_short_rejected_when_out_of_stock(config, single):
    cands = candidates_for(single, config, 10000, {"mercari": 15000}, stock_total=0)
    assert all(c.strategy != "short" for c in cands)


def test_mid_requires_history_and_release_window(config, single):
    # 発売 2026-06-01 → as_of で 99日 → 窓[30,90]外
    cands = candidates_for(single, config, 10000, {"mercari": 16000}, history_days=40, chg_30d=-0.1, chg_7d=0.03)
    assert all(c.strategy != "mid" for c in cands)
    recent = Product(**{**single.__dict__, "release_date": date(2026, 7, 20)})
    cands = candidates_for(recent, config, 10000, {"mercari": 16000}, history_days=40, chg_30d=-0.1, chg_7d=0.03)
    mid = next(c for c in cands if c.strategy == "mid")
    assert any("反転" in r for r in mid.reasons)


def test_long_requires_90_days_history(config, box):
    cands = candidates_for(box, config, 10000, {"mercari": 18000}, history_days=30, chg_90d=0.2, volatility=0.05)
    assert all(c.strategy != "long" for c in cands)
    cands = candidates_for(box, config, 10000, {"mercari": 18000}, history_days=120, chg_90d=0.2, volatility=0.05)
    long = next(c for c in cands if c.strategy == "long")
    assert any("供給停止" in r for r in long.reasons)


def test_risk_factor_discounts_ungraded_high_value(config, single):
    m = make_metrics(single, liquidity=0.5)
    cheap, _ = risk_factor(single, 10000, m, config)
    expensive, notes = risk_factor(single, 150000, m, config)
    assert expensive < cheap
    assert any("真贋" in n for n in notes)
    graded = Product(**{**single.__dict__, "graded": True})
    graded_factor, _ = risk_factor(graded, 150000, m, config)
    assert graded_factor > expensive


def _cand(config, pid, title, buy, sell, **kw):
    p = Product(product_id=pid, title=title, name=pid, form="single", release_date=date(2025, 1, 1))
    return candidates_for(p, config, buy, sell, **kw)


def test_select_daily_respects_cooldown_title_min_and_high_value_cap(config):
    pool = []
    # ポケカ5件（うち高額4件）＋ワンピ1件＋FW1件。どれも短期の条件を満たす
    for i in range(5):
        buy = 150000 if i < 4 else 10000
        sell = {"mercari": int(buy * 1.6)}
        pool += _cand(config, f"pkm{i}", "pokemon", buy, sell, liquidity=0.9 - i * 0.05)
    pool += _cand(config, "op0", "onepiece", 10000, {"mercari": 15000}, liquidity=0.2)
    pool += _cand(config, "fw0", "fusionworld", 10000, {"mercari": 15000}, liquidity=0.1)
    selected = select_daily(pool, config, excluded_product_ids={"pkm0"})
    short = selected["short"]
    ids = [c.product.product_id for c in short]
    assert "pkm0" not in ids                          # クールダウン除外
    assert "op0" in ids and "fw0" in ids              # タイトル最低1件
    assert sum(1 for c in short if c.profit.buy_price >= 100000) <= 3   # 高額品は最大3件
    assert len(short) <= 5
    assert selected["mid"] == [] and selected["long"] == []   # 水増ししない
    assert len(ids) == len(set(ids))
