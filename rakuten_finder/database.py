"""SQLite データベース。

将来 PostgreSQL へ移行しやすいよう、SQL は標準的な構文に留め、
アクセスはこのモジュールに集約する。

テーブル:
- products:       商品マスタ（item_code 単位）
- observations:   巡回ごとの観測（価格・相場・利益・スコア）
- decisions:      買う/見送り/保留 の記録（学習の元データ）
- notifications:  通知済み管理（重複通知防止）
- errors:         エラーログ
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .models import Candidate

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    item_code TEXT PRIMARY KEY,
    jan TEXT,
    name TEXT NOT NULL,
    keyword TEXT NOT NULL,
    genre_id TEXT,
    shop_name TEXT,
    url TEXT,
    image_url TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_code TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    rakuten_price INTEGER NOT NULL,
    shipping_in INTEGER NOT NULL DEFAULT 0,
    coupon INTEGER NOT NULL DEFAULT 0,
    point_total INTEGER NOT NULL DEFAULT 0,
    effective_cost INTEGER NOT NULL,
    sell_price INTEGER NOT NULL,
    mercari_fee INTEGER NOT NULL,
    shipping_out INTEGER NOT NULL,
    profit INTEGER NOT NULL,
    roi REAL NOT NULL,
    margin REAL NOT NULL,
    score REAL NOT NULL,
    rank TEXT NOT NULL,
    sold_count INTEGER NOT NULL DEFAULT 0,
    active_count INTEGER NOT NULL DEFAULT 0,
    stability REAL NOT NULL DEFAULT 1.0,
    in_stock INTEGER NOT NULL DEFAULT 1,
    warnings TEXT,
    FOREIGN KEY (item_code) REFERENCES products(item_code)
);

CREATE INDEX IF NOT EXISTS idx_obs_item ON observations(item_code, observed_at);
CREATE INDEX IF NOT EXISTS idx_obs_rank ON observations(rank, observed_at);

CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_code TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('buy', 'skip', 'hold')),
    note TEXT
);

CREATE INDEX IF NOT EXISTS idx_decisions_item ON decisions(item_code, decided_at);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dedupe_key TEXT NOT NULL UNIQUE,
    item_code TEXT NOT NULL,
    notified_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    context TEXT NOT NULL,
    message TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class FinderDatabase:
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

    # ---------- 保存 ----------

    def save_candidate(self, candidate: Candidate, observed_at: str | None = None) -> int:
        """products を upsert し observations に 1 行追加。observation id を返す。"""
        now = observed_at or utc_now()
        item, stats, profit, score = (
            candidate.item,
            candidate.stats,
            candidate.profit,
            candidate.score,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO products (
                    item_code, jan, name, keyword, genre_id, shop_name,
                    url, image_url, first_seen_at, last_seen_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(item_code) DO UPDATE SET
                    jan = excluded.jan,
                    name = excluded.name,
                    keyword = excluded.keyword,
                    shop_name = excluded.shop_name,
                    url = excluded.url,
                    image_url = excluded.image_url,
                    last_seen_at = excluded.last_seen_at
                """,
                (
                    item.item_code, item.jan, item.name, item.keyword,
                    item.genre_id, item.shop_name, item.url, item.image_url,
                    now, now,
                ),
            )
            cursor = conn.execute(
                """
                INSERT INTO observations (
                    item_code, observed_at, rakuten_price, shipping_in, coupon,
                    point_total, effective_cost, sell_price, mercari_fee,
                    shipping_out, profit, roi, margin, score, rank,
                    sold_count, active_count, stability, in_stock, warnings
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.item_code, now, profit.rakuten_price, profit.shipping_in,
                    profit.coupon, profit.point_total, profit.effective_cost,
                    profit.sell_price, profit.mercari_fee, profit.shipping_out,
                    profit.profit, profit.roi, profit.margin, score.score,
                    score.rank, stats.sold_count, stats.active_count,
                    stats.stability, 1 if item.in_stock else 0,
                    " / ".join(score.warnings) or None,
                ),
            )
            return int(cursor.lastrowid or 0)

    def insert_error(self, context: str, message: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO errors (occurred_at, context, message) VALUES (?, ?, ?)",
                (utc_now(), context, message[:1000]),
            )

    # ---------- 通知の重複防止 ----------

    def should_notify(self, dedupe_key: str, item_code: str, repeat_hours: int) -> bool:
        """同一 dedupe_key への通知が repeat_hours 以内なら False。"""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT notified_at FROM notifications WHERE dedupe_key = ?",
                (dedupe_key,),
            ).fetchone()
            if row:
                last = datetime.fromisoformat(row["notified_at"])
                if datetime.now(timezone.utc) - last < timedelta(hours=repeat_hours):
                    return False
            conn.execute(
                """
                INSERT INTO notifications (dedupe_key, item_code, notified_at)
                VALUES (?, ?, ?)
                ON CONFLICT(dedupe_key) DO UPDATE SET notified_at = excluded.notified_at
                """,
                (dedupe_key, item_code, utc_now()),
            )
            return True

    # ---------- 判断（学習） ----------

    def record_decision(self, item_code: str, decision: str, note: str = "") -> None:
        if decision not in ("buy", "skip", "hold"):
            raise ValueError(f"不正な判断: {decision}（buy/skip/hold のみ）")
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO decisions (item_code, decided_at, decision, note) VALUES (?, ?, ?, ?)",
                (item_code, utc_now(), decision, note),
            )

    def decision_stats_by_keyword(self) -> dict[str, dict[str, int]]:
        """スコアリングの学習ブースト用: キーワード別の buy/skip/hold 件数。"""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT p.keyword AS keyword, d.decision AS decision, COUNT(*) AS n
                FROM decisions d
                JOIN products p ON p.item_code = d.item_code
                GROUP BY p.keyword, d.decision
                """
            ).fetchall()
        stats: dict[str, dict[str, int]] = {}
        for row in rows:
            stats.setdefault(row["keyword"], {})[row["decision"]] = int(row["n"])
        return stats

    # ---------- ダッシュボード / レポート ----------

    def latest_observations(self, limit: int = 200) -> list[dict[str, Any]]:
        """商品ごとの最新観測（＋商品情報＋最新判断）を score 降順で返す。"""
        with self.connect() as conn:
            rows = conn.execute(
                """
                WITH latest AS (
                    SELECT item_code, MAX(id) AS id
                    FROM observations
                    GROUP BY item_code
                )
                SELECT
                    o.*, p.name, p.keyword, p.shop_name, p.url, p.image_url,
                    p.jan, p.genre_id,
                    (
                        SELECT d.decision FROM decisions d
                        WHERE d.item_code = o.item_code
                        ORDER BY d.decided_at DESC, d.id DESC LIMIT 1
                    ) AS decision,
                    EXISTS (
                        SELECT 1 FROM notifications n WHERE n.item_code = o.item_code
                    ) AS notified
                FROM observations o
                JOIN latest l ON l.id = o.id
                JOIN products p ON p.item_code = o.item_code
                ORDER BY o.score DESC, o.profit DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            return [dict(row) for row in rows]

    def daily_summary(self, since_iso: str) -> dict[str, Any]:
        """日次レポート用の集計。"""
        with self.connect() as conn:
            total = conn.execute(
                "SELECT COUNT(DISTINCT item_code) AS n FROM observations WHERE observed_at >= ?",
                (since_iso,),
            ).fetchone()["n"]
            s_rank = conn.execute(
                """
                SELECT o.item_code, p.name, MAX(o.profit) AS profit, o.roi, o.rank
                FROM observations o JOIN products p ON p.item_code = o.item_code
                WHERE o.observed_at >= ? AND o.rank = 'S'
                GROUP BY o.item_code ORDER BY profit DESC LIMIT 10
                """,
                (since_iso,),
            ).fetchall()
            top_profit = conn.execute(
                """
                SELECT o.item_code, p.name, MAX(o.profit) AS profit
                FROM observations o JOIN products p ON p.item_code = o.item_code
                WHERE o.observed_at >= ?
                GROUP BY o.item_code ORDER BY profit DESC LIMIT 5
                """,
                (since_iso,),
            ).fetchall()
            top_roi = conn.execute(
                """
                SELECT o.item_code, p.name, MAX(o.roi) AS roi
                FROM observations o JOIN products p ON p.item_code = o.item_code
                WHERE o.observed_at >= ?
                GROUP BY o.item_code ORDER BY roi DESC LIMIT 5
                """,
                (since_iso,),
            ).fetchall()
        return {
            "total": int(total),
            "s_rank": [dict(r) for r in s_rank],
            "top_profit": [dict(r) for r in top_profit],
            "top_roi": [dict(r) for r in top_roi],
        }
