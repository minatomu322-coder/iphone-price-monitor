from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

JST = timezone(timedelta(hours=9))


def now_jst() -> datetime:
    return datetime.now(JST).replace(microsecond=0)


def jst_iso() -> str:
    return now_jst().isoformat()


# ------------------------------------------------------------
# スキーマ（Phase 1-4: 収集・分析・CRM・通知の骨格）
# 後続Phase(投稿提案/実験/利益連携)のテーブルは差分マイグレーションで追加する。
# ------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    handle        TEXT NOT NULL UNIQUE,      -- @なしのスクリーンネーム
    name          TEXT,
    bio           TEXT,
    followers     INTEGER,
    following     INTEGER,
    genre         TEXT,                      -- 収集元ジャンル(ポケカ/せどり等)
    recent_posts  TEXT,                      -- 直近投稿の抜粋（分析材料）
    source        TEXT,                      -- seed_csv / api / manual
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- フォロワー数などの時系列（伸び代の判定に使う）
CREATE TABLE IF NOT EXISTS account_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL REFERENCES accounts(id),
    observed_at TEXT NOT NULL,
    followers   INTEGER,
    following   INTEGER,
    engagement  REAL
);
CREATE INDEX IF NOT EXISTS idx_snapshot ON account_snapshots(account_id, observed_at);

-- AI/ヒューリスティック採点の結果
CREATE TABLE IF NOT EXISTS scores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL REFERENCES accounts(id),
    analyzed_at TEXT NOT NULL,
    star        INTEGER NOT NULL,            -- 1-5
    total_score REAL NOT NULL,               -- 0-100
    axes_json   TEXT NOT NULL,               -- 各軸スコア(JSON)
    reason      TEXT,                        -- 交流すべき理由（人間が読む）
    engine      TEXT NOT NULL,               -- heuristic / claude
    confidence  REAL NOT NULL                -- 0-1
);
CREATE INDEX IF NOT EXISTS idx_score ON scores(account_id, analyzed_at);

-- CRM本体：全交流履歴
CREATE TABLE IF NOT EXISTS interactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id  INTEGER NOT NULL REFERENCES accounts(id),
    kind        TEXT NOT NULL,               -- like/reply/follow/dm/meet
    occurred_at TEXT NOT NULL,
    note        TEXT,
    source      TEXT                         -- discord_reaction / manual / csv
);
CREATE INDEX IF NOT EXISTS idx_interaction ON interactions(account_id, occurred_at);

-- 関係サマリ（親密度・次回推奨日）。interactionsから再計算される派生テーブル。
CREATE TABLE IF NOT EXISTS relationships (
    account_id           INTEGER PRIMARY KEY REFERENCES accounts(id),
    intimacy             REAL NOT NULL DEFAULT 0,   -- 0-100
    last_interaction_at  TEXT,
    next_recommended_at  TEXT,
    updated_at           TEXT NOT NULL
);

-- 実行ログ（監査・デバッグ用）
CREATE TABLE IF NOT EXISTS runs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    kind    TEXT NOT NULL,
    ran_at  TEXT NOT NULL,
    detail  TEXT
);
"""


class BrandDB:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    def log_run(self, kind: str, detail: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO runs (kind, ran_at, detail) VALUES (?, ?, ?)",
                (kind, jst_iso(), detail),
            )

    # ---------------- accounts ----------------
    def upsert_account(self, acc: dict[str, Any]) -> int:
        """handleをキーにUPSERT。既存フィールドは新値が空でなければ上書き。返り値=account_id。"""
        now = jst_iso()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE handle = ?", (acc["handle"],)
            ).fetchone()
            fields = ("name", "bio", "followers", "following", "genre", "recent_posts", "source")
            if row is None:
                conn.execute(
                    """INSERT INTO accounts
                       (handle, name, bio, followers, following, genre, recent_posts, source, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (
                        acc["handle"], acc.get("name"), acc.get("bio"),
                        acc.get("followers"), acc.get("following"), acc.get("genre"),
                        acc.get("recent_posts"), acc.get("source", "seed_csv"), now, now,
                    ),
                )
                account_id = int(conn.execute("SELECT last_insert_rowid() AS i").fetchone()["i"])
            else:
                account_id = int(row["id"])
                merged = {f: (acc.get(f) if acc.get(f) not in (None, "") else row[f]) for f in fields}
                conn.execute(
                    """UPDATE accounts SET name=?, bio=?, followers=?, following=?,
                       genre=?, recent_posts=?, source=?, updated_at=? WHERE id=?""",
                    (
                        merged["name"], merged["bio"], merged["followers"], merged["following"],
                        merged["genre"], merged["recent_posts"], merged["source"], now, account_id,
                    ),
                )
            # スナップショット（数値がある時だけ）
            if acc.get("followers") is not None:
                conn.execute(
                    "INSERT INTO account_snapshots (account_id, observed_at, followers, following, engagement) VALUES (?,?,?,?,?)",
                    (account_id, now, acc.get("followers"), acc.get("following"), acc.get("engagement")),
                )
        return account_id

    def get_account(self, account_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()

    def all_accounts(self) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()

    def follower_growth(self, account_id: int) -> float | None:
        """直近2スナップショットのフォロワー増加率(0-1想定)。データ不足はNone。"""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT followers FROM account_snapshots WHERE account_id=? AND followers IS NOT NULL ORDER BY observed_at DESC LIMIT 5",
                (account_id,),
            ).fetchall()
        if len(rows) < 2 or not rows[-1]["followers"]:
            return None
        newest, oldest = rows[0]["followers"], rows[-1]["followers"]
        return (newest - oldest) / max(oldest, 1)

    # ---------------- scores ----------------
    def save_score(self, account_id: int, result: dict[str, Any]) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO scores (account_id, analyzed_at, star, total_score, axes_json, reason, engine, confidence)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    account_id, jst_iso(), result["star"], result["total_score"],
                    json.dumps(result["axes"], ensure_ascii=False), result.get("reason", ""),
                    result.get("engine", "heuristic"), result.get("confidence", 0.5),
                ),
            )

    def latest_scores(self) -> dict[int, sqlite3.Row]:
        """account_idごとの最新スコア。"""
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT s.* FROM scores s
                   JOIN (SELECT account_id, MAX(analyzed_at) m FROM scores GROUP BY account_id) t
                     ON s.account_id=t.account_id AND s.analyzed_at=t.m"""
            ).fetchall()
        return {int(r["account_id"]): r for r in rows}

    # ---------------- interactions / relationships ----------------
    def add_interaction(self, account_id: int, kind: str, note: str = "", source: str = "manual") -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO interactions (account_id, kind, occurred_at, note, source) VALUES (?,?,?,?,?)",
                (account_id, kind, jst_iso(), note, source),
            )

    def last_interaction(self, account_id: int, kind: str | None = None) -> datetime | None:
        with self.connect() as conn:
            if kind:
                row = conn.execute(
                    "SELECT MAX(occurred_at) m FROM interactions WHERE account_id=? AND kind=?",
                    (account_id, kind),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT MAX(occurred_at) m FROM interactions WHERE account_id=?",
                    (account_id,),
                ).fetchone()
        return datetime.fromisoformat(row["m"]) if row and row["m"] else None

    def recompute_relationship(self, account_id: int, points: dict[str, int],
                               decay_per_day: float, cadence_days: dict[int, int]) -> dict[str, Any]:
        """interactions履歴から親密度・次回推奨日を再計算して保存する。"""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT kind, occurred_at FROM interactions WHERE account_id=? ORDER BY occurred_at",
                (account_id,),
            ).fetchall()
            score_row = conn.execute(
                "SELECT star FROM scores WHERE account_id=? ORDER BY analyzed_at DESC LIMIT 1",
                (account_id,),
            ).fetchone()
        now = now_jst()
        intimacy = 0.0
        last_at: datetime | None = None
        for r in rows:
            occurred = datetime.fromisoformat(r["occurred_at"])
            if last_at is not None:
                # 前回交流からの経過日数ぶん減衰させてから加点
                gap = (occurred - last_at).total_seconds() / 86400
                intimacy = max(0.0, intimacy - decay_per_day * gap)
            intimacy = min(100.0, intimacy + points.get(r["kind"], 0))
            last_at = occurred
        if last_at is not None:
            gap_now = (now - last_at).total_seconds() / 86400
            intimacy = max(0.0, intimacy - decay_per_day * gap_now)
        star = int(score_row["star"]) if score_row else 3
        cadence = cadence_days.get(star, 7)
        next_at = (last_at + timedelta(days=cadence)) if last_at else now
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO relationships (account_id, intimacy, last_interaction_at, next_recommended_at, updated_at)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(account_id) DO UPDATE SET
                     intimacy=excluded.intimacy,
                     last_interaction_at=excluded.last_interaction_at,
                     next_recommended_at=excluded.next_recommended_at,
                     updated_at=excluded.updated_at""",
                (
                    account_id, round(intimacy, 1),
                    last_at.isoformat() if last_at else None,
                    next_at.isoformat(), now.isoformat(),
                ),
            )
        return {"intimacy": round(intimacy, 1),
                "last_interaction_at": last_at.isoformat() if last_at else None,
                "next_recommended_at": next_at.isoformat()}

    def relationship(self, account_id: int) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM relationships WHERE account_id=?", (account_id,)
            ).fetchone()
