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

-- 発見台帳: いつ・どのソースから・どのURLを根拠に候補を見つけたか（重複ヒットも記録）
CREATE TABLE IF NOT EXISTS discoveries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id    INTEGER NOT NULL REFERENCES accounts(id),
    source        TEXT NOT NULL,       -- seed_csv / note_rss / (将来: web_search, youtube, x_api)
    source_detail TEXT,                -- テーマ・ジャンル等
    source_url    TEXT,                -- 取得元URL（CEO承認条件5）
    discovered_at TEXT NOT NULL,
    is_duplicate  INTEGER NOT NULL DEFAULT 0,
    evidence      TEXT                 -- クロス媒体統合時の証拠URL（本人明記リンク）
);
CREATE INDEX IF NOT EXISTS idx_discovery ON discoveries(discovered_at, source);

-- 実行ログ（監査・デバッグ用）
CREATE TABLE IF NOT EXISTS runs (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    kind    TEXT NOT NULL,
    ran_at  TEXT NOT NULL,
    detail  TEXT
);

-- ============ Proof & Personality Engine ============

-- 投稿候補（朝便=proof系 / 夜便=personality系）と投稿履歴
CREATE TABLE IF NOT EXISTS post_drafts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    slot        TEXT NOT NULL,               -- morning / night
    post_type   TEXT NOT NULL,               -- proof / decision / personality / learning
    title       TEXT,
    body        TEXT NOT NULL,
    source      TEXT,                        -- iphone_price / memo / template / claude
    status      TEXT NOT NULL DEFAULT 'proposed',  -- proposed / posted / skipped
    posted_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_drafts ON post_drafts(created_at, post_type, status);

-- Personality素材（1行メモ：失敗/学び/実話/考え方）
CREATE TABLE IF NOT EXISTS memos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    kind        TEXT NOT NULL,               -- fail / learn / story / thought
    text        TEXT NOT NULL,
    used        INTEGER NOT NULL DEFAULT 0
);

-- 投稿ごとのKPI（Xアナリティクスから手入力。実験モードの主データ）
CREATE TABLE IF NOT EXISTS post_kpis (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id      INTEGER NOT NULL REFERENCES post_drafts(id),
    recorded_at   TEXT NOT NULL,
    impressions   INTEGER,
    likes         INTEGER,
    replies       INTEGER,
    bookmarks     INTEGER,
    profile_views INTEGER,
    follows       INTEGER
);

-- 日次のアカウント指標（週2回程度の手入力でOK。ブランド分析の主データ）
CREATE TABLE IF NOT EXISTS daily_metrics (
    date             TEXT PRIMARY KEY,        -- YYYY-MM-DD
    followers        INTEGER,
    profile_views    INTEGER,
    dms_received     INTEGER,
    replies_received INTEGER,
    note             TEXT
);
"""


# 候補者ステータス（CEO仕様③）。record時に自動昇格、降格はしない。
STATUSES = ("NEW", "DISCOVERED", "ENGAGED", "FOLLOWED", "ACTIVE", "NOT_INTERESTED", "ARCHIVED")
STATUS_RANK = {s: i for i, s in enumerate(("NEW", "DISCOVERED", "ENGAGED", "FOLLOWED", "ACTIVE"))}

# 既存DBへ後方互換で列を足す差分マイグレーション（存在すればスキップ）
MIGRATIONS = [
    "ALTER TABLE accounts ADD COLUMN url TEXT",
    "ALTER TABLE accounts ADD COLUMN medium TEXT DEFAULT 'x'",
    "ALTER TABLE accounts ADD COLUMN status TEXT NOT NULL DEFAULT 'NEW'",
    "ALTER TABLE accounts ADD COLUMN last_notified_at TEXT",
    "ALTER TABLE accounts ADD COLUMN notify_count INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE accounts ADD COLUMN reevaluate INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE accounts ADD COLUMN profile_hash TEXT",
]


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
            for stmt in MIGRATIONS:
                try:
                    conn.execute(stmt)
                except sqlite3.OperationalError:
                    pass  # 列が既にある

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
                conn.execute(
                    "UPDATE accounts SET url=?, medium=COALESCE(?, medium) WHERE id=?",
                    (acc.get("url") or f"https://x.com/{acc['handle']}", acc.get("medium"), account_id),
                )
            else:
                account_id = int(row["id"])
                merged = {f: (acc.get(f) if acc.get(f) not in (None, "") else row[f]) for f in fields}
                conn.execute(
                    """UPDATE accounts SET name=?, bio=?, followers=?, following=?,
                       genre=?, recent_posts=?, source=?, updated_at=?,
                       url=COALESCE(?, url), medium=COALESCE(?, medium) WHERE id=?""",
                    (
                        merged["name"], merged["bio"], merged["followers"], merged["following"],
                        merged["genre"], merged["recent_posts"], merged["source"], now,
                        acc.get("url"), acc.get("medium"), account_id,
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

    # ---------------- ステータス / 通知ローテーション（CEO仕様②③④） ----------------

    @staticmethod
    def profile_hash(acc: dict[str, Any] | sqlite3.Row) -> str:
        """プロフィール変更検知用ハッシュ（name/bio/genreが変わると再通知許可）。"""
        import hashlib

        raw = f"{acc['name']}|{acc['bio']}|{acc['genre']}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def set_status(self, account_id: int, status: str) -> None:
        if status not in STATUSES:
            raise ValueError(f"不正なステータス: {status}（{'/'.join(STATUSES)}）")
        with self.connect() as conn:
            conn.execute("UPDATE accounts SET status=?, updated_at=? WHERE id=?",
                         (status, jst_iso(), account_id))

    def promote_status(self, account_id: int, new_status: str) -> None:
        """交流に応じた自動昇格。降格はしない。NOT_INTERESTED/ARCHIVEDは触らない。"""
        with self.connect() as conn:
            row = conn.execute("SELECT status FROM accounts WHERE id=?", (account_id,)).fetchone()
        if row is None:
            return
        cur = row["status"]
        if cur not in STATUS_RANK or new_status not in STATUS_RANK:
            return
        if STATUS_RANK[new_status] > STATUS_RANK[cur]:
            self.set_status(account_id, new_status)

    def mark_reevaluate(self, account_id: int) -> None:
        """CEO再評価指定：90日ルールを飛ばして次回通知対象に戻す。"""
        with self.connect() as conn:
            conn.execute("UPDATE accounts SET reevaluate=1 WHERE id=?", (account_id,))

    def eligible_for_notification(self, renotify_days: int) -> tuple[list[sqlite3.Row], int]:
        """通知可能な候補と、90日ルールで除外された人数を返す。

        再通知の解禁条件（CEO仕様④）:
          未通知 / CEO再評価指定 / プロフィール変更 / 前回通知からrenotify_days経過
        """
        now = now_jst()
        eligible, excluded = [], 0
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM accounts WHERE status NOT IN ('NOT_INTERESTED','ARCHIVED')"
            ).fetchall()
        for r in rows:
            if r["last_notified_at"] is None or r["reevaluate"]:
                eligible.append(r)
                continue
            if r["profile_hash"] and self.profile_hash(r) != r["profile_hash"]:
                eligible.append(r)
                continue
            last = datetime.fromisoformat(r["last_notified_at"])
            if (now - last).days >= renotify_days:
                eligible.append(r)
            else:
                excluded += 1
        return eligible, excluded

    def mark_notified(self, account_id: int) -> None:
        """通知済みに更新：日時・回数・プロフハッシュを記録し、NEWはDISCOVEREDへ。"""
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
            if row is None:
                return
            conn.execute(
                """UPDATE accounts SET last_notified_at=?, notify_count=notify_count+1,
                   reevaluate=0, profile_hash=?,
                   status=CASE WHEN status='NEW' THEN 'DISCOVERED' ELSE status END
                   WHERE id=?""",
                (jst_iso(), self.profile_hash(row), account_id),
            )

    def notified_today(self) -> list[sqlite3.Row]:
        today = now_jst().date().isoformat()
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM accounts WHERE substr(last_notified_at,1,10)=?", (today,)
            ).fetchall()

    def add_discovery(self, account_id: int, source: str, source_detail: str | None,
                      source_url: str | None, is_duplicate: bool, evidence: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO discoveries (account_id, source, source_detail, source_url,
                   discovered_at, is_duplicate, evidence) VALUES (?,?,?,?,?,?,?)""",
                (account_id, source, source_detail, source_url, jst_iso(),
                 1 if is_duplicate else 0, evidence),
            )

    def discovery_stats_today(self) -> dict[str, Any]:
        """ソース別の本日取得数と重複数（ダッシュボード用）。"""
        today = now_jst().date().isoformat()
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT source, COUNT(*) total, SUM(is_duplicate) dup
                   FROM discoveries WHERE substr(discovered_at,1,10)=? GROUP BY source""",
                (today,),
            ).fetchall()
        by_source = {r["source"]: {"total": int(r["total"]), "dup": int(r["dup"] or 0)} for r in rows}
        return {
            "by_source": by_source,
            "found_today": sum(v["total"] for v in by_source.values()),
            "dup_today": sum(v["dup"] for v in by_source.values()),
        }

    # KPIファネル: 通知コホートに対する各イベントの独立転換率（CEO承認条件4）
    # 一本道の到達ステージではなく、イベントごとに独立集計する。
    FUNNEL_KINDS = ("like", "reply", "reply_received", "follow", "followback",
                    "dm", "consult", "deal")

    def kpi_funnel(self) -> dict[str, Any]:
        with self.connect() as conn:
            candidates = conn.execute("SELECT COUNT(*) c FROM accounts").fetchone()["c"]
            notified = conn.execute(
                "SELECT COUNT(*) c FROM accounts WHERE notify_count > 0"
            ).fetchone()["c"]
            rows = conn.execute(
                """SELECT i.kind, COUNT(DISTINCT i.account_id) c
                   FROM interactions i JOIN accounts a ON a.id = i.account_id
                   WHERE a.notify_count > 0 GROUP BY i.kind"""
            ).fetchall()
        events = {k: 0 for k in self.FUNNEL_KINDS}
        for r in rows:
            if r["kind"] in events:
                events[r["kind"]] = int(r["c"])
        rates = {k: (v / notified if notified else 0.0) for k, v in events.items()}
        return {"candidates": int(candidates), "notified": int(notified),
                "events": events, "rates": rates}

    def dashboard_stats(self) -> dict[str, int]:
        """ダッシュボード用の集計（CEO仕様⑤）。通知人数・重複除外は呼び出し側が足す。"""
        today = now_jst().date().isoformat()
        with self.connect() as conn:
            total = conn.execute("SELECT COUNT(*) c FROM accounts").fetchone()["c"]
            new_today = conn.execute(
                "SELECT COUNT(*) c FROM accounts WHERE substr(created_at,1,10)=?", (today,)
            ).fetchone()["c"]
            by_status = {r["status"]: r["c"] for r in conn.execute(
                "SELECT status, COUNT(*) c FROM accounts GROUP BY status"
            ).fetchall()}
        return {
            "db_total": int(total),
            "new_today": int(new_today),
            "active": int(by_status.get("ACTIVE", 0)),
            "archived": int(by_status.get("ARCHIVED", 0)),
        }
