from __future__ import annotations

"""交流候補の選定ロジック（AIが「今日交流する価値が最も高い人」を選ぶ中核）。

方針:
    - 深く・狭く。直近で交流した相手はクールダウンで外し、薄い連投を防ぐ。
    - 再交流推奨日(next_recommended_at)を過ぎた既存関係を優先的に拾い直す。
    - フォロワー数ではなく★(=ブランド相性・コンサル見込み中心)で並べる。
"""

import json
from datetime import timedelta
from typing import Any

from .db import BrandDB, now_jst


def _within(db: BrandDB, account_id: int, kind: str, days: int) -> bool:
    last = db.last_interaction(account_id, kind)
    if last is None:
        return False
    return (now_jst() - last).total_seconds() < days * 86400


def build_candidates(db: BrandDB, cfg: dict[str, Any]) -> dict[str, Any]:
    scores = db.latest_scores()
    notify = cfg["notify"]
    rows: list[dict[str, Any]] = []
    now = now_jst()
    for acc in db.all_accounts():
        sid = int(acc["id"])
        sc = scores.get(sid)
        if sc is None:
            continue
        rel = db.relationship(sid)
        axes = json.loads(sc["axes_json"])
        due = False
        if rel and rel["next_recommended_at"]:
            due = rel["next_recommended_at"] <= now.isoformat()
        rows.append({
            "id": sid,
            "handle": acc["handle"],
            "name": acc["name"] or acc["handle"],
            "star": int(sc["star"]),
            "total": float(sc["total_score"]),
            "reason": sc["reason"],
            "engine": sc["engine"],
            "consult_ltv": axes.get("consult_ltv", 0),
            "intimacy": float(rel["intimacy"]) if rel else 0.0,
            "due": due,
            "recent_posts": acc["recent_posts"] or "",
        })

    # いいね候補: クールダウン外を★→総合点で。上位 like_count 人。
    like_pool = [r for r in rows if not _within(db, r["id"], "like", notify["like_cooldown_days"])]
    like_pool.sort(key=lambda r: (r["star"], r["total"]), reverse=True)
    likes = like_pool[: notify["like_count"]]

    # リプ候補: 再交流期限切れ→★→親密度の低さ(伸びしろ)で。reply_max 人。
    reply_pool = [r for r in rows if not _within(db, r["id"], "reply", notify["reply_cooldown_days"])]
    reply_pool.sort(key=lambda r: (r["due"], r["star"], -r["intimacy"]), reverse=True)
    replies = reply_pool[: notify["reply_max"]]

    # 最重要人物: ★→コンサル見込み
    top = max(rows, key=lambda r: (r["star"], r["consult_ltv"]), default=None)

    # フォロー/DM推奨（必要な場合のみ）: ★5で未フォロー/未DMの相手
    follow_sugg = [r for r in rows if r["star"] == 5 and not db.last_interaction(r["id"], "follow")][:3]
    dm_sugg = [r for r in rows if r["star"] == 5 and r["intimacy"] >= 30
               and not db.last_interaction(r["id"], "dm")][:3]

    return {"likes": likes, "replies": replies, "top": top,
            "follow": follow_sugg, "dm": dm_sugg,
            "reply_min": notify["reply_min"], "reply_max": notify["reply_max"]}
