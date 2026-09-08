from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import Observation


SCHEMA = """
-- 日次価格スナップショット（買値・売値の両方をここに入れる）
CREATE TABLE IF NOT EXISTS product_prices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    observed_on TEXT NOT NULL,          -- JSTの日付(YYYY-MM-DD)。日次集計キー
    product_id TEXT NOT NULL,
    source TEXT NOT NULL,
    side TEXT NOT NULL,                 -- buy / sell
    channel TEXT NOT NULL,
    condition TEXT,
    price INTEGER NOT NULL,
    stock INTEGER,
    sold_count_30d INTEGER,
    url TEXT NOT NULL,
    raw_text TEXT
);
CREATE INDEX IF NOT EXISTS idx_product_prices_lookup
ON product_prices(product_id, side, observed_on);

-- 日次の集計指標
CREATE TABLE IF NOT EXISTS product_metrics (
    as_of TEXT NOT NULL,
    product_id TEXT NOT NULL,
    buy_min INTEGER,
    buy_source TEXT,
    stock_total INTEGER,
    listing_count INTEGER,
    sell_kaitori INTEGER,
    sell_mercari INTEGER,
    sell_yahoo INTEGER,
    chg_7d REAL, chg_30d REAL, chg_90d REAL,
    volatility REAL,
    history_days INTEGER,
    liquidity REAL,
    PRIMARY KEY (as_of, product_id)
);

-- 提出履歴（重複提出の防止＋実績フィードバック）
CREATE TABLE IF NOT EXISTS submissions (
    submitted_on TEXT NOT NULL,
    product_id TEXT NOT NULL,
    strategy TEXT NOT NULL,
    buy_price INTEGER,
    channel TEXT,
    expect_sell INTEGER,
    expect_profit INTEGER,
    score REAL,
    result TEXT,                        -- adopted / rejected / bought / sold（人が後から記入）
    actual_profit INTEGER,
    PRIMARY KEY (submitted_on, product_id)
);

CREATE TABLE IF NOT EXISTS fetch_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    source TEXT NOT NULL,
    product_id TEXT,
    url TEXT,
    error TEXT NOT NULL
);
"""

JST = timezone(timedelta(hours=9))


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def today_jst() -> date:
    return datetime.now(JST).date()


class TcgDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---- 価格 ----
    def insert_observations(self, observations: list[Observation], observed_on: date) -> int:
        rows = [
            (
                obs.observed_at or utc_now(), observed_on.isoformat(), obs.product_id,
                obs.source, obs.side, obs.channel, obs.condition, int(obs.price),
                obs.stock, obs.sold_count_30d, obs.url, (obs.raw_text or "")[:500],
            )
            for obs in observations
        ]
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO product_prices (
                    observed_at, observed_on, product_id, source, side, channel,
                    condition, price, stock, sold_count_30d, url, raw_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
        return len(rows)

    def delete_observations(self, product_id: str, source: str, observed_on: date) -> None:
        """同一日・同一ソースの再取得時に古い行を消して重複を防ぐ。"""
        with self.connect() as conn:
            conn.execute(
                "DELETE FROM product_prices WHERE product_id = ? AND source = ? AND observed_on = ?",
                (product_id, source, observed_on.isoformat()),
            )

    def observations_on(self, product_id: str, observed_on: date) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM product_prices WHERE product_id = ? AND observed_on = ?",
                (product_id, observed_on.isoformat()),
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_sell_observations(self, product_id: str, max_age_days: int, as_of: date) -> list[dict[str, Any]]:
        """sell側は毎日更新されない（手動シート等）ため、チャネルごとに直近の1件を返す。"""
        since = (as_of - timedelta(days=max_age_days)).isoformat()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT p.* FROM product_prices p
                JOIN (
                    SELECT channel, MAX(observed_on) AS observed_on
                    FROM product_prices
                    WHERE product_id = ? AND side = 'sell' AND observed_on BETWEEN ? AND ?
                    GROUP BY channel
                ) latest ON p.channel = latest.channel AND p.observed_on = latest.observed_on
                WHERE p.product_id = ? AND p.side = 'sell'
                ORDER BY p.channel, p.price DESC
                """,
                (product_id, since, as_of.isoformat(), product_id),
            ).fetchall()
        return [dict(row) for row in rows]

    def daily_buy_min_series(self, product_id: str, days: int, as_of: date) -> list[tuple[date, int]]:
        """日ごとの最安仕入価格の系列（古い順）。トレンド計算用。"""
        since = (as_of - timedelta(days=days)).isoformat()
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT observed_on, MIN(price) AS price
                FROM product_prices
                WHERE product_id = ? AND side = 'buy' AND observed_on BETWEEN ? AND ?
                GROUP BY observed_on ORDER BY observed_on
                """,
                (product_id, since, as_of.isoformat()),
            ).fetchall()
        return [(date.fromisoformat(row["observed_on"]), int(row["price"])) for row in rows]

    def history_days(self, product_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(DISTINCT observed_on) AS n FROM product_prices WHERE product_id = ? AND side = 'buy'",
                (product_id,),
            ).fetchone()
        return int(row["n"]) if row else 0

    # ---- 指標 ----
    def upsert_metrics(self, rows: list[dict[str, Any]]) -> None:
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO product_metrics (
                    as_of, product_id, buy_min, buy_source, stock_total, listing_count,
                    sell_kaitori, sell_mercari, sell_yahoo,
                    chg_7d, chg_30d, chg_90d, volatility, history_days, liquidity
                ) VALUES (
                    :as_of, :product_id, :buy_min, :buy_source, :stock_total, :listing_count,
                    :sell_kaitori, :sell_mercari, :sell_yahoo,
                    :chg_7d, :chg_30d, :chg_90d, :volatility, :history_days, :liquidity
                )
                ON CONFLICT(as_of, product_id) DO UPDATE SET
                    buy_min = excluded.buy_min, buy_source = excluded.buy_source,
                    stock_total = excluded.stock_total, listing_count = excluded.listing_count,
                    sell_kaitori = excluded.sell_kaitori, sell_mercari = excluded.sell_mercari,
                    sell_yahoo = excluded.sell_yahoo,
                    chg_7d = excluded.chg_7d, chg_30d = excluded.chg_30d, chg_90d = excluded.chg_90d,
                    volatility = excluded.volatility, history_days = excluded.history_days,
                    liquidity = excluded.liquidity
                """,
                rows,
            )

    # ---- 提出履歴 ----
    def recently_submitted(self, as_of: date, cooldown_days: int) -> set[str]:
        since = (as_of - timedelta(days=cooldown_days)).isoformat()
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT product_id FROM submissions WHERE submitted_on >= ? AND submitted_on < ?",
                (since, as_of.isoformat()),
            ).fetchall()
        return {row["product_id"] for row in rows}

    def record_submissions(self, rows: list[dict[str, Any]]) -> None:
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO submissions (
                    submitted_on, product_id, strategy, buy_price, channel,
                    expect_sell, expect_profit, score
                ) VALUES (
                    :submitted_on, :product_id, :strategy, :buy_price, :channel,
                    :expect_sell, :expect_profit, :score
                )
                ON CONFLICT(submitted_on, product_id) DO UPDATE SET
                    strategy = excluded.strategy, buy_price = excluded.buy_price,
                    channel = excluded.channel, expect_sell = excluded.expect_sell,
                    expect_profit = excluded.expect_profit, score = excluded.score
                """,
                rows,
            )

    def insert_error(self, source: str, product_id: str | None, url: str | None, error: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO fetch_errors (observed_at, source, product_id, url, error) VALUES (?, ?, ?, ?, ?)",
                (utc_now(), source, product_id, url, error[:1000]),
            )
