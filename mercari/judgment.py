from __future__ import annotations

from typing import Any

from mercari.db import MercariDatabase


# 判断の答え合わせ（判断OSの中核）。
# ChatGPTの過去の判断と実際の結果を突き合わせ、正誤を自動評価する。
# 評価結果は次回の仕入れ判断出力に「判断の成績」として渡り、
# ChatGPT自身が自分の判断精度を踏まえて次の判断を出せるようにする。

BUY_WORDS = ("買い", "条件付き")
SKIP_WORDS = ("見送り",)

ACCURACY_LABELS = {"correct": "正しい", "incorrect": "誤り", "partial": "部分的に正しい"}


def _is_buy(verdict: str) -> bool:
    return any(word in verdict for word in BUY_WORDS) and "見送り" not in verdict


def _is_skip(verdict: str) -> bool:
    return any(word in verdict for word in SKIP_WORDS)


def evaluate_review(
    db: MercariDatabase, review: dict[str, Any], config: dict[str, Any]
) -> tuple[str, str] | None:
    """1件の判断を自動評価する。評価できたら (outcome, accuracy) を返す。

    評価ルール:
    - 「買い」判断 → その商品が売れたら答え合わせ（黒字=正しい / 赤字=誤り）
    - 「見送り」判断 → 判断当時と比べて相場中央値が±10%以上動いたら答え合わせ
      （上昇=誤り(機会損失) / 下落=正しい）
    - まだ結果が出ていないものは評価しない（Noneを返す）
    """
    verdict = review.get("verdict") or ""
    item_id = review.get("item_id")
    if not item_id or review.get("kind") not in ("sourcing", "stale"):
        return None
    move_ratio = float(
        config.get("thresholds", {}).get("judgement_market_move", 0.10)
    )

    if _is_buy(verdict):
        sale = db.sale_for_item(int(item_id))
        if not sale:
            return None
        item = db.get_item(int(item_id)) or {}
        cost = int(item.get("purchase_price") or 0) + int(item.get("purchase_shipping") or 0)
        profit = (
            int(sale["sold_price"]) - int(sale["sales_fee"])
            - int(sale["shipping_cost"]) - int(sale["other_cost"]) - cost
        )
        days = sale.get("days_to_sell")
        days_text = f"・{days}日で売却" if days is not None else ""
        if profit > 0:
            return (f"利益{profit:+,}円で売却{days_text}", "correct")
        if profit == 0:
            return (f"利益0円で売却{days_text}", "partial")
        return (f"赤字{profit:+,}円で売却{days_text}", "incorrect")

    if _is_skip(verdict):
        then = db.market_at(int(item_id), review["created_at"])
        now = db.latest_market(int(item_id))
        if not then or not now or then["id"] == now["id"]:
            return None
        if not then.get("median_price") or not now.get("median_price"):
            return None
        change = (now["median_price"] - then["median_price"]) / then["median_price"]
        pct = round(change * 100)
        if change >= move_ratio:
            return (f"見送り後に相場中央値が{pct:+d}%上昇（機会損失の可能性）", "incorrect")
        if change <= -move_ratio:
            return (f"見送り後に相場中央値が{pct:+d}%下落", "correct")
        return None

    return None


def auto_evaluate(db: MercariDatabase, config: dict[str, Any]) -> int:
    """未評価の判断をまとめて自動評価する。評価できた件数を返す（何度呼んでも安全）。"""
    evaluated = 0
    for review in db.unevaluated_reviews():
        result = evaluate_review(db, review, config)
        if result:
            outcome, accuracy = result
            db.record_review_outcome(int(review["id"]), outcome, accuracy)
            evaluated += 1
    return evaluated


def judgment_stats(db: MercariDatabase) -> dict[str, Any]:
    """ChatGPT判断の成績集計（種別ごとの正解率・自信度と正誤の関係）。"""
    reviews = [r for r in db.all_reviews() if r.get("verdict")]
    evaluated = [r for r in reviews if r.get("accuracy")]
    by_kind: dict[str, dict[str, int]] = {}
    for r in evaluated:
        bucket = by_kind.setdefault(r["kind"], {"correct": 0, "incorrect": 0, "partial": 0})
        bucket[r["accuracy"]] = bucket.get(r["accuracy"], 0) + 1

    # 高自信（70%以上）だった判断の正解率（自信度の較正チェック）
    high_conf = [r for r in evaluated if (r.get("confidence") or 0) >= 70]
    high_conf_correct = sum(1 for r in high_conf if r["accuracy"] == "correct")

    return {
        "total": len(reviews),
        "evaluated": len(evaluated),
        "correct": sum(1 for r in evaluated if r["accuracy"] == "correct"),
        "incorrect": sum(1 for r in evaluated if r["accuracy"] == "incorrect"),
        "partial": sum(1 for r in evaluated if r["accuracy"] == "partial"),
        "by_kind": by_kind,
        "high_confidence_total": len(high_conf),
        "high_confidence_correct": high_conf_correct,
    }


def judgment_stats_text(db: MercariDatabase) -> str:
    """仕入れ判断出力へ埋め込む成績サマリー（ChatGPTが自分の精度を把握するため）。"""
    stats = judgment_stats(db)
    if not stats["evaluated"]:
        return ""
    parts = [
        f"答え合わせ済み{stats['evaluated']}件中 "
        f"正しい{stats['correct']}件・誤り{stats['incorrect']}件・部分的{stats['partial']}件"
    ]
    if stats["high_confidence_total"]:
        parts.append(
            f"自信度70%以上の判断は{stats['high_confidence_total']}件中"
            f"{stats['high_confidence_correct']}件が正解"
        )
    return "／".join(parts)
