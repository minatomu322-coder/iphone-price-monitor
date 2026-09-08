from __future__ import annotations

from datetime import date, timedelta

from tcg.database import TcgDatabase
from tcg.metrics import change_since, compute_metrics, liquidity_score, volatility
from tcg.models import Observation


AS_OF = date(2026, 9, 8)


def series(days: int, start: int, step: int):
    return [(AS_OF - timedelta(days=days - i), start + step * i) for i in range(days + 1)]


def test_change_since_uses_nearest_past_point():
    s = series(30, 10000, 100)   # 30日前 10,000 → 当日 13,000
    assert change_since(s, AS_OF, 30) == 0.3
    assert change_since(s, AS_OF, 7) == round(700 / 12300, 4)
    assert change_since(s, AS_OF, 90) is None      # 履歴不足
    assert change_since([], AS_OF, 7) is None


def test_change_since_requires_today():
    s = series(30, 10000, 100)[:-1]
    assert change_since(s, AS_OF, 7) is None


def test_volatility_needs_5_points():
    assert volatility(series(3, 10000, 0), AS_OF, 30) is None
    assert volatility(series(10, 10000, 0), AS_OF, 30) == 0.0
    assert volatility(series(10, 10000, 500), AS_OF, 30) > 0


def test_liquidity_score():
    assert liquidity_score(30, 0) == 1.0
    assert liquidity_score(15, 0) == 0.5
    assert liquidity_score(None, 10) == 0.5
    assert liquidity_score(None, 0) == 0.0


def test_compute_metrics_from_db(tmp_path, config, single):
    db = TcgDatabase(tmp_path / "t.sqlite3")
    for i in range(8):
        day = AS_OF - timedelta(days=7 - i)
        db.insert_observations(
            [
                Observation(single.product_id, "rakuten", "buy", "rakuten", 10000 + i * 100, "u1"),
                Observation(single.product_id, "surugaya", "buy", "surugaya", 10500 + i * 100, "u2", stock=3),
            ],
            day,
        )
    # sell側は5日前に一度だけ（手動シート想定）
    db.insert_observations(
        [Observation(single.product_id, "manual_sheet", "sell", "mercari", 15000, "sheet", sold_count_30d=12)],
        AS_OF - timedelta(days=5),
    )
    db.insert_observations(
        [Observation(single.product_id, "surugaya", "sell", "kaitori", 11000, "k")], AS_OF
    )
    m = compute_metrics(db, single, AS_OF, config)
    assert m.buy_min == 10700 and m.buy_source == "rakuten"
    assert m.stock_total == 3 and m.listing_count == 2
    assert m.sell_prices == {"mercari": 15000, "kaitori": 11000}
    assert m.sold_count_30d == 12
    assert m.chg_7d == 0.07
    assert m.chg_30d is None
    assert m.history_days == 8
    assert m.liquidity == 0.4


def test_delete_observations_prevents_duplicates(tmp_path, single):
    db = TcgDatabase(tmp_path / "t.sqlite3")
    obs = [Observation(single.product_id, "rakuten", "buy", "rakuten", 100, "u")]
    db.insert_observations(obs, AS_OF)
    db.delete_observations(single.product_id, "rakuten", AS_OF)
    db.insert_observations(obs, AS_OF)
    assert len(db.observations_on(single.product_id, AS_OF)) == 1


def test_recently_submitted_window(tmp_path):
    db = TcgDatabase(tmp_path / "t.sqlite3")
    db.record_submissions([
        {"submitted_on": (AS_OF - timedelta(days=3)).isoformat(), "product_id": "a", "strategy": "short",
         "buy_price": 1, "channel": "mercari", "expect_sell": 2, "expect_profit": 1, "score": 0.5},
        {"submitted_on": (AS_OF - timedelta(days=20)).isoformat(), "product_id": "b", "strategy": "short",
         "buy_price": 1, "channel": "mercari", "expect_sell": 2, "expect_profit": 1, "score": 0.5},
    ])
    assert db.recently_submitted(AS_OF, 14) == {"a"}
