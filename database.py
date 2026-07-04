from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS price_observations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    item_name TEXT NOT NULL,
    shop_name TEXT NOT NULL,
    color_key TEXT NOT NULL,
    color_label TEXT NOT NULL,
    capacity TEXT,
    state TEXT,
    price INTEGER NOT NULL,
    previous_price INTEGER,
    diff INTEGER,
    source_updated_at TEXT,
    url TEXT NOT NULL,
    raw_text TEXT
);

CREATE INDEX IF NOT EXISTS idx_price_lookup
ON price_observations(item_name, shop_name, color_key, observed_at);

CREATE INDEX IF NOT EXISTS idx_price_best
ON price_observations(item_name, color_key, observed_at);

CREATE TABLE IF NOT EXISTS scrape_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at TEXT NOT NULL,
    shop_name TEXT NOT NULL,
    url TEXT NOT NULL,
    error TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alert_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    alert_key TEXT NOT NULL UNIQUE,
    last_sent_at TEXT NOT NULL
);
"""


class PriceDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def get_previous_price(self, item_name: str, shop_name: str, color_key: str) -> int | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT price
                FROM price_observations
                WHERE item_name = ? AND shop_name = ? AND color_key = ?
                ORDER BY observed_at DESC, id DESC
                LIMIT 1
                """,
                (item_name, shop_name, color_key),
            ).fetchone()
            return int(row["price"]) if row else None

    def insert_price(self, record: dict[str, Any]) -> dict[str, Any]:
        previous_price = self.get_previous_price(
            record["item_name"], record["shop_name"], record["color_key"]
        )
        diff = None if previous_price is None else record["price"] - previous_price
        saved = {
            **record,
            "observed_at": record.get("observed_at") or utc_now(),
            "previous_price": previous_price,
            "diff": diff,
        }
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO price_observations (
                    observed_at, item_name, shop_name, color_key, color_label,
                    capacity, state, price, previous_price, diff,
                    source_updated_at, url, raw_text
                ) VALUES (
                    :observed_at, :item_name, :shop_name, :color_key, :color_label,
                    :capacity, :state, :price, :previous_price, :diff,
                    :source_updated_at, :url, :raw_text
                )
                """,
                saved,
            )
        return saved

    def insert_error(self, shop_name: str, url: str, error: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO scrape_errors (observed_at, shop_name, url, error)
                VALUES (?, ?, ?, ?)
                """,
                (utc_now(), shop_name, url, error[:1000]),
            )

    def should_send_alert(self, alert_key: str, repeat_hours: int) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT last_sent_at FROM alert_events WHERE alert_key = ?",
                (alert_key,),
            ).fetchone()
            if row:
                last_sent = datetime.fromisoformat(row["last_sent_at"])
                if datetime.now(timezone.utc) - last_sent < timedelta(hours=repeat_hours):
                    return False
            conn.execute(
                """
                INSERT INTO alert_events (alert_key, last_sent_at)
                VALUES (?, ?)
                ON CONFLICT(alert_key) DO UPDATE SET last_sent_at = excluded.last_sent_at
                """,
                (alert_key, utc_now()),
            )
            return True

    def latest_by_color(self, item_name: str, color_key: str) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                """
                WITH latest AS (
                    SELECT shop_name, MAX(observed_at) AS observed_at
                    FROM price_observations
                    WHERE item_name = ? AND color_key = ?
                    GROUP BY shop_name
                )
                SELECT p.*
                FROM price_observations p
                JOIN latest l
                  ON p.shop_name = l.shop_name
                 AND p.observed_at = l.observed_at
                WHERE p.item_name = ? AND p.color_key = ?
                ORDER BY p.price DESC
                """,
                (item_name, color_key, item_name, color_key),
            ).fetchall()


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
