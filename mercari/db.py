from __future__ import annotations

import shutil
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


JST = timezone(timedelta(hours=9))

# 商品ライフサイクル: candidate(仕入れ候補) -> purchased(仕入れ済み)
#                  -> listed(出品中) -> sold(売却済み) / discarded(見送り・処分)
ITEM_STATUSES = ("candidate", "purchased", "listed", "sold", "discarded")
LISTING_STATUSES = ("draft", "active", "sold", "cancelled")

# ChatGPT判断履歴の種別
GPT_REVIEW_KINDS = ("sourcing", "listing", "stale", "monthly", "reply", "other")
# 売れなかった理由の標準タグ（detailで自由記述も可能）
UNSOLD_REASON_TAGS = (
    "価格が高い", "写真が悪い", "タイトルが弱い", "説明不足",
    "需要が少ない", "季節外れ", "相場下落", "供給過多", "状態が悪い", "その他",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate',
    name TEXT NOT NULL,
    model_number TEXT,
    jan_code TEXT,
    brand TEXT,
    category TEXT,
    condition TEXT,
    accessories TEXT,
    flaws TEXT,
    images_note TEXT,
    notes TEXT,
    purchase_price INTEGER,
    purchase_shipping INTEGER NOT NULL DEFAULT 0,
    purchase_source TEXT,
    purchase_url TEXT,
    purchased_at TEXT,
    planned_price INTEGER,
    min_price INTEGER,
    sales_policy TEXT,
    shipping_method TEXT,
    shipping_cost INTEGER NOT NULL DEFAULT 0,
    shipping_days TEXT
);

CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    captured_at TEXT NOT NULL,
    source TEXT,
    sold_count INTEGER,
    active_count INTEGER,
    min_price INTEGER,
    median_price INTEGER,
    mean_price INTEGER,
    max_price INTEGER,
    url TEXT,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS idx_market_item ON market_snapshots(item_id, captured_at);

CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    title TEXT,
    description TEXT,
    category TEXT,
    condition_label TEXT,
    list_price INTEGER,
    current_price INTEGER,
    listed_at TEXT,
    ended_at TEXT,
    views INTEGER NOT NULL DEFAULT 0,
    likes INTEGER NOT NULL DEFAULT 0,
    comments INTEGER NOT NULL DEFAULT 0,
    shipping_method TEXT,
    shipping_days TEXT
);
CREATE INDEX IF NOT EXISTS idx_listing_item ON listings(item_id);

CREATE TABLE IF NOT EXISTS price_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id INTEGER NOT NULL REFERENCES listings(id),
    changed_at TEXT NOT NULL,
    old_price INTEGER,
    new_price INTEGER NOT NULL,
    reason TEXT
);

CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    listing_id INTEGER REFERENCES listings(id),
    sold_at TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'mercari',
    sold_price INTEGER NOT NULL,
    sales_fee INTEGER NOT NULL,
    shipping_cost INTEGER NOT NULL DEFAULT 0,
    other_cost INTEGER NOT NULL DEFAULT 0,
    note TEXT
);
CREATE INDEX IF NOT EXISTS idx_sales_date ON sales(sold_at);

CREATE TABLE IF NOT EXISTS improvements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    applied_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT,
    result TEXT
);

-- ChatGPTの判断履歴（仕入れレビュー・出品文・売れ残り分析などの回答を貼って保存）
CREATE TABLE IF NOT EXISTS gpt_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER REFERENCES items(id),
    created_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    verdict TEXT,
    summary TEXT,
    raw_text TEXT
);
CREATE INDEX IF NOT EXISTS idx_gpt_reviews_item ON gpt_reviews(item_id, created_at);

-- 売れなかった理由（ユーザー入力またはChatGPT分析の結論をタグで蓄積し、後で集計する）
CREATE TABLE IF NOT EXISTS unsold_reasons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    recorded_at TEXT NOT NULL,
    reason_tag TEXT NOT NULL,
    detail TEXT,
    source TEXT NOT NULL DEFAULT 'user'
);
CREATE INDEX IF NOT EXISTS idx_unsold_item ON unsold_reasons(item_id, recorded_at);

-- 将来のOpenAI API連携時に利用料金を記録する（初期MVPでは未使用）
CREATE TABLE IF NOT EXISTS api_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    used_at TEXT NOT NULL,
    purpose TEXT NOT NULL,
    model TEXT,
    cost_yen REAL,
    note TEXT
);
"""

ITEM_FIELDS = (
    "status", "name", "model_number", "jan_code", "brand", "category",
    "condition", "accessories", "flaws", "images_note", "notes",
    "purchase_price", "purchase_shipping", "purchase_source", "purchase_url",
    "purchased_at", "planned_price", "min_price", "sales_policy",
    "shipping_method", "shipping_cost", "shipping_days",
)

LISTING_FIELDS = (
    "item_id", "status", "title", "description", "category", "condition_label",
    "list_price", "current_price", "listed_at", "ended_at",
    "views", "likes", "comments", "shipping_method", "shipping_days",
)


def now_jst() -> str:
    return datetime.now(JST).replace(microsecond=0).isoformat()


def today_jst() -> str:
    return datetime.now(JST).date().isoformat()


class MercariDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    def _migrate(self, conn: sqlite3.Connection) -> None:
        """既存DBへの後方互換マイグレーション（列追加のみ・破壊的変更はしない）。"""
        self._ensure_column(conn, "sales", "days_to_sell", "INTEGER")
        # 改善のライフサイクル: proposed(提案のみ) -> applied(実施) / rejected(不採用)
        self._ensure_column(conn, "improvements", "status", "TEXT NOT NULL DEFAULT 'applied'")
        self._ensure_column(conn, "improvements", "source", "TEXT NOT NULL DEFAULT 'user'")

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl_type: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ---------- items ----------

    def upsert_item(self, data: dict[str, Any]) -> int:
        item_id = data.get("id")
        values = {key: data.get(key) for key in ITEM_FIELDS}
        if values.get("status") and values["status"] not in ITEM_STATUSES:
            raise ValueError(f"不正なstatus: {values['status']}")
        # 未指定(None)は「変更しない」の意味なので、指定された時だけ数値化する
        for key in ("purchase_shipping", "shipping_cost"):
            if values.get(key) is not None:
                values[key] = int(values[key])
        with self.connect() as conn:
            if item_id:
                current = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
                if not current:
                    raise ValueError(f"item {item_id} が見つかりません")
                merged = {**dict(current), **{k: v for k, v in values.items() if v is not None}}
                merged["updated_at"] = now_jst()
                assignments = ", ".join(f"{key} = :{key}" for key in (*ITEM_FIELDS, "updated_at"))
                conn.execute(f"UPDATE items SET {assignments} WHERE id = :id", {**merged, "id": item_id})
                return int(item_id)
            if not values.get("name"):
                raise ValueError("商品名(name)は必須です")
            values.setdefault("status", "candidate")
            values["status"] = values["status"] or "candidate"
            values["purchase_shipping"] = int(values.get("purchase_shipping") or 0)
            values["shipping_cost"] = int(values.get("shipping_cost") or 0)
            values["created_at"] = values["updated_at"] = now_jst()
            columns = ", ".join((*ITEM_FIELDS, "created_at", "updated_at"))
            placeholders = ", ".join(f":{key}" for key in (*ITEM_FIELDS, "created_at", "updated_at"))
            cur = conn.execute(f"INSERT INTO items ({columns}) VALUES ({placeholders})", values)
            return int(cur.lastrowid)

    def get_item(self, item_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
            return dict(row) if row else None

    def list_items(self, status: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM items"
        params: tuple[Any, ...] = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY id DESC"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def set_item_status(self, item_id: int, status: str) -> None:
        if status not in ITEM_STATUSES:
            raise ValueError(f"不正なstatus: {status}")
        with self.connect() as conn:
            conn.execute(
                "UPDATE items SET status = ?, updated_at = ? WHERE id = ?",
                (status, now_jst(), item_id),
            )

    # ---------- market snapshots ----------

    def insert_market_snapshot(self, data: dict[str, Any]) -> int:
        record = {
            "item_id": int(data["item_id"]),
            "captured_at": data.get("captured_at") or now_jst(),
            "source": data.get("source") or "メルカリ",
            "sold_count": _opt_int(data.get("sold_count")),
            "active_count": _opt_int(data.get("active_count")),
            "min_price": _opt_int(data.get("min_price")),
            "median_price": _opt_int(data.get("median_price")),
            "mean_price": _opt_int(data.get("mean_price")),
            "max_price": _opt_int(data.get("max_price")),
            "url": data.get("url"),
            "notes": data.get("notes"),
        }
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO market_snapshots (
                    item_id, captured_at, source, sold_count, active_count,
                    min_price, median_price, mean_price, max_price, url, notes
                ) VALUES (
                    :item_id, :captured_at, :source, :sold_count, :active_count,
                    :min_price, :median_price, :mean_price, :max_price, :url, :notes
                )
                """,
                record,
            )
            return int(cur.lastrowid)

    def latest_market(self, item_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM market_snapshots
                WHERE item_id = ?
                ORDER BY captured_at DESC, id DESC
                LIMIT 1
                """,
                (item_id,),
            ).fetchone()
            return dict(row) if row else None

    def market_history(self, item_id: int, limit: int = 12) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM market_snapshots
                WHERE item_id = ?
                ORDER BY captured_at DESC, id DESC
                LIMIT ?
                """,
                (item_id, limit),
            ).fetchall()
            return [dict(row) for row in reversed(rows)]

    # ---------- listings ----------

    def upsert_listing(self, data: dict[str, Any]) -> int:
        listing_id = data.get("id")
        values = {key: data.get(key) for key in LISTING_FIELDS}
        if values.get("status") and values["status"] not in LISTING_STATUSES:
            raise ValueError(f"不正なstatus: {values['status']}")
        with self.connect() as conn:
            if listing_id:
                current = conn.execute(
                    "SELECT * FROM listings WHERE id = ?", (listing_id,)
                ).fetchone()
                if not current:
                    raise ValueError(f"listing {listing_id} が見つかりません")
                merged = {**dict(current), **{k: v for k, v in values.items() if v is not None}}
                merged["updated_at"] = now_jst()
                assignments = ", ".join(f"{key} = :{key}" for key in (*LISTING_FIELDS, "updated_at"))
                conn.execute(
                    f"UPDATE listings SET {assignments} WHERE id = :id",
                    {**merged, "id": listing_id},
                )
                return int(listing_id)
            if not values.get("item_id"):
                raise ValueError("item_idは必須です")
            values["status"] = values.get("status") or "draft"
            values["views"] = int(values.get("views") or 0)
            values["likes"] = int(values.get("likes") or 0)
            values["comments"] = int(values.get("comments") or 0)
            values["created_at"] = values["updated_at"] = now_jst()
            columns = ", ".join((*LISTING_FIELDS, "created_at", "updated_at"))
            placeholders = ", ".join(f":{key}" for key in (*LISTING_FIELDS, "created_at", "updated_at"))
            cur = conn.execute(f"INSERT INTO listings ({columns}) VALUES ({placeholders})", values)
            return int(cur.lastrowid)

    def get_listing(self, listing_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
            return dict(row) if row else None

    def listings_for_item(self, item_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM listings WHERE item_id = ? ORDER BY id DESC", (item_id,)
            ).fetchall()
            return [dict(row) for row in rows]

    def active_listing(self, item_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM listings
                WHERE item_id = ? AND status IN ('active', 'draft')
                ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END, id DESC
                LIMIT 1
                """,
                (item_id,),
            ).fetchone()
            return dict(row) if row else None

    def record_price_change(
        self, listing_id: int, new_price: int, reason: str | None = None
    ) -> None:
        with self.connect() as conn:
            listing = conn.execute(
                "SELECT current_price, list_price FROM listings WHERE id = ?", (listing_id,)
            ).fetchone()
            if not listing:
                raise ValueError(f"listing {listing_id} が見つかりません")
            old_price = listing["current_price"] or listing["list_price"]
            conn.execute(
                """
                INSERT INTO price_changes (listing_id, changed_at, old_price, new_price, reason)
                VALUES (?, ?, ?, ?, ?)
                """,
                (listing_id, now_jst(), old_price, int(new_price), reason),
            )
            conn.execute(
                "UPDATE listings SET current_price = ?, updated_at = ? WHERE id = ?",
                (int(new_price), now_jst(), listing_id),
            )

    def price_changes_for_listing(self, listing_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM price_changes WHERE listing_id = ? ORDER BY changed_at",
                (listing_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    # ---------- sales ----------

    def record_sale(self, data: dict[str, Any]) -> int:
        record = {
            "item_id": int(data["item_id"]),
            "listing_id": _opt_int(data.get("listing_id")),
            "sold_at": data.get("sold_at") or now_jst(),
            "channel": data.get("channel") or "mercari",
            "sold_price": int(data["sold_price"]),
            "sales_fee": int(data["sales_fee"]),
            "shipping_cost": int(data.get("shipping_cost") or 0),
            "other_cost": int(data.get("other_cost") or 0),
            "note": data.get("note"),
        }
        # 売却までの日数（回転日数）を記録時に確定させる
        item = self.get_item(record["item_id"])
        record["days_to_sell"] = _days_diff(
            (item or {}).get("purchased_at"), record["sold_at"]
        )
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO sales (
                    item_id, listing_id, sold_at, channel, sold_price,
                    sales_fee, shipping_cost, other_cost, note, days_to_sell
                ) VALUES (
                    :item_id, :listing_id, :sold_at, :channel, :sold_price,
                    :sales_fee, :shipping_cost, :other_cost, :note, :days_to_sell
                )
                """,
                record,
            )
            sale_id = int(cur.lastrowid)
            conn.execute(
                "UPDATE items SET status = 'sold', updated_at = ? WHERE id = ?",
                (now_jst(), record["item_id"]),
            )
            if record["listing_id"]:
                conn.execute(
                    "UPDATE listings SET status = 'sold', ended_at = ?, updated_at = ? WHERE id = ?",
                    (record["sold_at"], now_jst(), record["listing_id"]),
                )
            return sale_id

    def sales_between(self, date_from: str, date_to: str) -> list[dict[str, Any]]:
        """date_from〜date_to（両端含む・日付文字列比較）の販売履歴を商品情報付きで返す。"""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT s.*, i.name AS item_name, i.category, i.purchase_source,
                       i.purchase_price, i.purchase_shipping, i.purchased_at
                FROM sales s
                JOIN items i ON i.id = s.item_id
                WHERE substr(s.sold_at, 1, 10) BETWEEN ? AND ?
                ORDER BY s.sold_at
                """,
                (date_from, date_to),
            ).fetchall()
            return [dict(row) for row in rows]

    # ---------- ChatGPT判断履歴 ----------

    def add_gpt_review(self, data: dict[str, Any]) -> int:
        kind = data.get("kind") or "other"
        if kind not in GPT_REVIEW_KINDS:
            raise ValueError(f"不正なkind: {kind}（{'/'.join(GPT_REVIEW_KINDS)}のいずれか）")
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO gpt_reviews (item_id, created_at, kind, verdict, summary, raw_text)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    _opt_int(data.get("item_id")),
                    data.get("created_at") or now_jst(),
                    kind,
                    data.get("verdict"),
                    data.get("summary"),
                    data.get("raw_text"),
                ),
            )
            return int(cur.lastrowid)

    def gpt_reviews_for_item(self, item_id: int, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM gpt_reviews
                WHERE item_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT ?
                """,
                (item_id, limit),
            ).fetchall()
            return [dict(row) for row in rows]

    def latest_gpt_verdict(self, item_id: int) -> dict[str, Any] | None:
        reviews = self.gpt_reviews_for_item(item_id, limit=1)
        return reviews[0] if reviews else None

    # ---------- 売れなかった理由 ----------

    def add_unsold_reason(self, data: dict[str, Any]) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO unsold_reasons (item_id, recorded_at, reason_tag, detail, source)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(data["item_id"]),
                    data.get("recorded_at") or now_jst(),
                    data.get("reason_tag") or "その他",
                    data.get("detail"),
                    data.get("source") or "user",
                ),
            )
            return int(cur.lastrowid)

    def unsold_reasons_for_item(self, item_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM unsold_reasons WHERE item_id = ? ORDER BY recorded_at",
                (item_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    def unsold_reason_stats(
        self, date_from: str | None = None, date_to: str | None = None
    ) -> list[dict[str, Any]]:
        """理由タグごとの件数集計（期間指定は記録日ベース）。"""
        query = """
            SELECT reason_tag, COUNT(*) AS count
            FROM unsold_reasons
        """
        params: list[Any] = []
        if date_from and date_to:
            query += " WHERE substr(recorded_at, 1, 10) BETWEEN ? AND ?"
            params = [date_from, date_to]
        query += " GROUP BY reason_tag ORDER BY count DESC"
        with self.connect() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    # ---------- improvements ----------

    def add_improvement(self, data: dict[str, Any]) -> int:
        status = data.get("status") or "applied"
        if status not in ("proposed", "applied", "rejected"):
            raise ValueError(f"不正なstatus: {status}（proposed/applied/rejectedのいずれか）")
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO improvements (item_id, applied_at, kind, detail, result, status, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(data["item_id"]),
                    data.get("applied_at") or now_jst(),
                    data.get("kind") or "その他",
                    data.get("detail"),
                    data.get("result"),
                    status,
                    data.get("source") or "user",
                ),
            )
            return int(cur.lastrowid)

    def update_improvement_status(
        self, improvement_id: int, status: str, result: str | None = None
    ) -> None:
        if status not in ("proposed", "applied", "rejected"):
            raise ValueError(f"不正なstatus: {status}")
        with self.connect() as conn:
            if result is not None:
                conn.execute(
                    "UPDATE improvements SET status = ?, result = ? WHERE id = ?",
                    (status, result, improvement_id),
                )
            else:
                conn.execute(
                    "UPDATE improvements SET status = ? WHERE id = ?", (status, improvement_id)
                )

    def improvements_for_item(self, item_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM improvements WHERE item_id = ? ORDER BY applied_at",
                (item_id,),
            ).fetchall()
            return [dict(row) for row in rows]

    # ---------- backup ----------

    def backup(self, backup_dir: str | Path) -> Path:
        backup_dir = Path(backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(JST).strftime("%Y%m%d-%H%M%S")
        dest = backup_dir / f"{self.path.stem}-{stamp}{self.path.suffix}"
        shutil.copy2(self.path, dest)
        return dest


def _opt_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def _days_diff(start: str | None, end: str | None) -> int | None:
    """日付文字列（ISO/日時どちらでも）同士の日数差。どちらか欠けたらNone。"""
    if not start or not end:
        return None
    try:
        return (date.fromisoformat(str(end)[:10]) - date.fromisoformat(str(start)[:10])).days
    except ValueError:
        return None
