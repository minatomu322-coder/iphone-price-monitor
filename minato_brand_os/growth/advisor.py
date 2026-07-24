from __future__ import annotations

"""Advisor — ダッシュボードに「どうすれば改善するか」を表示するためのロジック。

不足人数を出すだけでなく、原因の内訳と、具体的な次のアクション＋見込み効果を提案する。
見込みは実測値（Source別のフィードあたり収量）があればそれを使い、無ければ保守的な既定値。
"""

from typing import Any

from ..db import BrandDB

# フィード1本あたりの候補者収量の既定見込み（実測が無い時に使う保守値）
DEFAULT_YIELD = {"note_rss": (1, 3), "blog_rss": (1, 1), "youtube_rss": (1, 1)}
MAGAZINE_YIELD = (5, 15)  # noteマガジンRSSは複数書き手のため高収量


def _feed_count(cfg: dict[str, Any], source: str) -> int:
    return len((cfg.get("growth", {}).get("sources", {}).get(source, {}) or {}).get("feeds", []))


def _measured_yield(db: BrandDB, cfg: dict[str, Any], source: str) -> float | None:
    n_feeds = _feed_count(cfg, source)
    if not n_feeds:
        return None
    for s in db.source_analysis():
        if s["source"] == source and s["unique"] > 0:
            return s["unique"] / n_feeds
    return None


def build_advice(db: BrandDB, cfg: dict[str, Any], cand: dict[str, Any]) -> list[str]:
    advice: list[str] = []
    notify_cfg = cfg["notify"]

    # 1. RSSフィードの状況と増強見込み
    for source, label in (("note_rss", "note RSS"), ("blog_rss", "ブログRSS"), ("youtube_rss", "YouTube RSS")):
        n = _feed_count(cfg, source)
        measured = _measured_yield(db, cfg, source)
        lo, hi = DEFAULT_YIELD[source]
        if measured:
            est = f"実測: 1フィードあたり約{measured:.1f}人"
        else:
            est = f"見込み: 1フィードあたり{lo}〜{hi}人"
        if n == 0:
            advice.append(f"{label}未登録 → 5件追加で約{lo*5}〜{hi*5}人増の見込み（config追記のみ）")
        else:
            per = measured or (lo + hi) / 2
            advice.append(f"{label} {n}件登録済（{est}）→ +5件で約{per*5:.0f}人増の見込み")

    # noteマガジンRSSの推し（複数書き手のため効率が段違い）
    advice.append(f"noteマガジンRSSは1本で{MAGAZINE_YIELD[0]}〜{MAGAZINE_YIELD[1]}人（複数書き手）。最優先で追加を")

    # 2. 90日通知制限の解禁予定
    unlocks = db.renotify_unlock_schedule(int(notify_cfg.get("renotify_days", 90)))
    if cand.get("excluded_dup"):
        if unlocks:
            sched = " / ".join(f"{d}日後に{n}人" for d, n in unlocks[:3])
            advice.append(f"90日通知制限中 {cand['excluded_dup']}人（解禁予定: {sched}）")
        else:
            advice.append(f"90日通知制限中 {cand['excluded_dup']}人（直近14日の解禁なし）")

    # 3. 手動系
    patrol_n = int(cfg.get("growth", {}).get("patrol_per_day", 3))
    advice.append(f"巡回支援: 1日{patrol_n}アカウント×1人収穫で週+{patrol_n * 7}人（昼便の🚶欄から）")
    return advice
