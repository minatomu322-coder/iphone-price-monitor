from __future__ import annotations

"""交流候補の選定ロジック（AIが「今日交流する価値が最も高い人」を選ぶ中核）。

CEO仕様:
    ① 通知は総合スコア上位 daily_count(30) 人のみ。重複水増し禁止。
    ②④ 一度通知した人物は renotify_days(90) 日間再通知禁止。
        解禁は「CEO再評価指定 / プロフィール変更 / 90日経過」のみ。
    候補が30人に満たない場合は不足数を隠さず返す（shortfall）。
"""

import json
from typing import Any

from .db import BrandDB, now_jst


def _row_to_cand(db: BrandDB, acc, sc) -> dict[str, Any]:
    axes = json.loads(sc["axes_json"])
    rel = db.relationship(int(acc["id"]))
    return {
        "id": int(acc["id"]),
        "handle": acc["handle"],
        "name": acc["name"] or acc["handle"],
        "url": acc["url"] or f"https://x.com/{acc['handle']}",
        "status": acc["status"],
        "star": int(sc["star"]),
        "total": float(sc["total_score"]),
        "reason": sc["reason"],
        "engine": sc["engine"],
        "consult_ltv": axes.get("consult_ltv", 0),
        "intimacy": float(rel["intimacy"]) if rel else 0.0,
        "recent_posts": acc["recent_posts"] or "",
    }


def build_candidates(db: BrandDB, cfg: dict[str, Any]) -> dict[str, Any]:
    notify = cfg["notify"]
    scores = db.latest_scores()
    daily_count = int(notify.get("daily_count", 30))
    renotify_days = int(notify.get("renotify_days", 90))

    # --- 通知候補（昼便）: 90日ルール適用 → スコア上位30人 ---
    eligible_rows, excluded_dup = db.eligible_for_notification(renotify_days)
    pool = [_row_to_cand(db, r, scores[int(r["id"])]) for r in eligible_rows
            if int(r["id"]) in scores]
    pool.sort(key=lambda c: (c["star"], c["total"]), reverse=True)
    likes = pool[:daily_count]
    shortfall = max(0, daily_count - len(likes))
    # 不足時は原因を正直に説明する（水増し・重複・低品質での補充はしない）
    shortfall_reason = ""
    if shortfall:
        total = len(db.all_accounts())
        parts = [f"DB総数{total}人のうち未通知・通知可能が{len(pool)}人"]
        if excluded_dup:
            parts.append(f"90日ルールで{excluded_dup}人除外中")
        parts.append("→ 対策: シード追加 / note RSS登録 / 巡回支援で収穫 / APIキー投入")
        shortfall_reason = "。".join(parts)

    # --- リプ候補（夜便）: 既に通知済みの相手から。新規人物は夜便で増やさない ---
    reply_pool = []
    for acc in db.all_accounts():
        sid = int(acc["id"])
        if sid not in scores or acc["status"] in ("NOT_INTERESTED", "ARCHIVED"):
            continue
        if acc["last_notified_at"] is None:
            continue  # 未通知の人物をリプ便で実質通知しない（重複管理の一元化）
        last_reply = db.last_interaction(sid, "reply")
        if last_reply and (now_jst() - last_reply).days < int(notify["reply_cooldown_days"]):
            continue
        reply_pool.append(_row_to_cand(db, acc, scores[sid]))
    reply_pool.sort(key=lambda c: (c["star"], -c["intimacy"], c["total"]), reverse=True)
    replies = reply_pool[: int(notify["reply_max"])]

    # --- 最重要人物 / フォロー / DM 推奨 ---
    ranked_all = sorted(
        (c for c in ([*likes, *reply_pool])), key=lambda c: (c["star"], c["consult_ltv"]), reverse=True
    )
    top = ranked_all[0] if ranked_all else None
    follow_sugg = [c for c in likes if c["star"] >= 4
                   and not db.last_interaction(c["id"], "follow")][:3]
    dm_sugg = [c for c in reply_pool if c["star"] >= 4 and c["intimacy"] >= 30
               and not db.last_interaction(c["id"], "dm")][:3]

    return {
        "likes": likes,
        "shortfall": shortfall,          # 30人に届かない不足数（水増しせず正直に出す）
        "shortfall_reason": shortfall_reason,
        "excluded_dup": excluded_dup,    # 90日ルールで除外された人数
        "replies": replies,
        "top": top,
        "follow": follow_sugg,
        "dm": dm_sugg,
        "reply_min": notify["reply_min"],
        "reply_max": notify["reply_max"],
    }
