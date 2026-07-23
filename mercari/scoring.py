from __future__ import annotations

from typing import Any

from mercari.db import MercariDatabase


# 売却実績のスコアリング（100点満点 → S〜D）。
# ROI（最大40点）・利益額（最大30点）・回転日数（最大30点）の合計から
# 資金拘束ペナルティを引く。利益が出ても長期間資金を寝かせた売却は高評価にしない。
# ラベルの基準はChatGPTと相談して調整しやすいよう1か所にまとめる。
ROI_POINTS = ((0.30, 40), (0.20, 32), (0.15, 25), (0.05, 15), (0.0001, 8))
PROFIT_POINTS = ((5000, 30), (3000, 24), (1500, 18), (500, 10), (1, 5))
DAYS_POINTS = ((7, 30), (14, 24), (30, 16), (60, 8))
DAYS_POINTS_OVER = 3     # 61日以上
DAYS_POINTS_UNKNOWN = 10  # 仕入れ日未入力で回転日数が不明（中立点）

# 資金拘束ペナルティ（回転日数と拘束資金の組み合わせで減点）
CAPITAL_PENALTY_DAYS = ((180, 25), (90, 15), (61, 8))   # 長期化そのものへの減点
CAPITAL_PENALTY_LARGE = 10        # 高額仕入れ（下記基準以上）が31日超寝た場合の追加減点
CAPITAL_PENALTY_LARGE_COST = 30000
CAPITAL_PENALTY_LARGE_DAYS = 30

GRADES = ((85, "S"), (70, "A"), (55, "B"), (40, "C"))


def _tier(value: float, tiers: tuple[tuple[float, int], ...], default: int = 0) -> int:
    for threshold, points in tiers:
        if value >= threshold:
            return points
    return default


def capital_penalty(cost: int, days_to_sell: int | None) -> int:
    """資金拘束ペナルティ（減点）。利益が出ても資金を長く寝かせた売却は評価を下げる。"""
    if days_to_sell is None:
        return 0
    penalty = 0
    for threshold, points in CAPITAL_PENALTY_DAYS:
        if days_to_sell >= threshold:
            penalty = points
            break
    if cost >= CAPITAL_PENALTY_LARGE_COST and days_to_sell > CAPITAL_PENALTY_LARGE_DAYS:
        penalty += CAPITAL_PENALTY_LARGE
    return penalty


def score_sale(
    profit: int, roi: float | None, days_to_sell: int | None, cost: int = 0
) -> dict[str, Any]:
    """1件の売却実績を採点する（資金拘束ペナルティ込み）。"""
    roi_points = _tier(roi, ROI_POINTS) if roi is not None else 0
    profit_points = _tier(profit, PROFIT_POINTS)
    if days_to_sell is None:
        days_points = DAYS_POINTS_UNKNOWN
    else:
        days_points = DAYS_POINTS_OVER
        for threshold, points in DAYS_POINTS:
            if days_to_sell <= threshold:
                days_points = points
                break
    penalty = capital_penalty(cost, days_to_sell)
    total = max(0, roi_points + profit_points + days_points - penalty)
    grade = "D"
    for threshold, label in GRADES:
        if total >= threshold:
            grade = label
            break
    return {
        "points": total,
        "grade": grade,
        "breakdown": {
            "roi": roi_points, "profit": profit_points, "days": days_points,
            "capital_penalty": -penalty,
        },
    }


def product_key(item_like: dict[str, Any]) -> str:
    """同一商品をまとめるキー。型番があれば型番、なければ商品名で判定する。"""
    model = (item_like.get("model_number") or "").strip().lower()
    if model:
        return f"model:{model}"
    return "name:" + (item_like.get("item_name") or item_like.get("name") or "").strip().lower()


def scored_sales(db: MercariDatabase) -> list[dict[str, Any]]:
    """全売却履歴に利益・ROI・スコアを付けて返す。"""
    sales = db.sales_between("0000-01-01", "9999-12-31")
    results = []
    for s in sales:
        item = db.get_item(s["item_id"]) or {}
        cost = int(s.get("purchase_price") or 0) + int(s.get("purchase_shipping") or 0)
        profit = (
            int(s["sold_price"]) - int(s["sales_fee"])
            - int(s["shipping_cost"]) - int(s["other_cost"]) - cost
        )
        roi = round(profit / cost, 3) if cost > 0 else None
        score = score_sale(profit, roi, s.get("days_to_sell"), cost)
        results.append({
            **s,
            "model_number": item.get("model_number"),
            "category": s.get("category") or item.get("category"),
            "cost": cost,
            "profit": profit,
            "roi": roi,
            **score,
        })
    return results


def repeat_candidates(
    db: MercariDatabase, config: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """リピート仕入れすべき商品の自動判定。

    同一商品（型番または商品名）の売却実績をまとめ、
    「全て黒字・平均ROIが基準以上・平均回転が基準以内」ならリピート推奨。
    """
    config = config or {}
    thresholds = config.get("thresholds", {})
    min_roi = float(thresholds.get("repeat_min_roi", thresholds.get("min_roi", 0.15)))
    max_days = int(thresholds.get("repeat_max_days", 30))

    groups: dict[str, dict[str, Any]] = {}
    for sale in scored_sales(db):
        key = product_key(sale)
        group = groups.setdefault(key, {
            "key": key,
            "name": sale.get("item_name"),
            "model_number": sale.get("model_number"),
            "category": sale.get("category"),
            "sales": [],
        })
        group["sales"].append(sale)

    results = []
    for group in groups.values():
        sales = group["sales"]
        rois = [s["roi"] for s in sales if s["roi"] is not None]
        days = [s["days_to_sell"] for s in sales if s.get("days_to_sell") is not None]
        points = [s["points"] for s in sales]
        avg_roi = round(sum(rois) / len(rois), 3) if rois else None
        avg_days = round(sum(days) / len(days), 1) if days else None
        loss_count = sum(1 for s in sales if s["profit"] < 0)
        recommend = (
            loss_count == 0
            and avg_roi is not None and avg_roi >= min_roi
            and (avg_days is None or avg_days <= max_days)
        )
        avg_points = sum(points) / len(points)
        grade = "D"
        for threshold, label in GRADES:
            if avg_points >= threshold:
                grade = label
                break
        results.append({
            "key": group["key"],
            "name": group["name"],
            "model_number": group["model_number"],
            "category": group["category"],
            "sold_count": len(sales),
            "total_profit": sum(s["profit"] for s in sales),
            "avg_roi": avg_roi,
            "avg_days_to_sell": avg_days,
            "loss_count": loss_count,
            "grade": grade,
            "recommend_repeat": recommend,
        })
    results.sort(key=lambda g: (-int(g["recommend_repeat"]), -g["total_profit"]))
    return results


PRICE_BANDS = (
    ("〜2,999円", 0, 2999),
    ("3,000〜9,999円", 3000, 9999),
    ("10,000〜29,999円", 10000, 29999),
    ("30,000円〜", 30000, None),
)


def profitability_features(db: MercariDatabase) -> dict[str, list[dict[str, Any]]]:
    """「どんな商品が利益になりやすいか」をChatGPTが学習・分析するための集計。"""
    sales = scored_sales(db)

    def aggregate(group_fn) -> list[dict[str, Any]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for s in sales:
            groups.setdefault(group_fn(s) or "未分類", []).append(s)
        rows = []
        for label, members in groups.items():
            rois = [m["roi"] for m in members if m["roi"] is not None]
            days = [m["days_to_sell"] for m in members if m.get("days_to_sell") is not None]
            rows.append({
                "label": label,
                "count": len(members),
                "total_profit": sum(m["profit"] for m in members),
                "avg_roi": round(sum(rois) / len(rois), 3) if rois else None,
                "avg_days": round(sum(days) / len(days), 1) if days else None,
                "loss_rate": round(sum(1 for m in members if m["profit"] < 0) / len(members), 3),
            })
        rows.sort(key=lambda r: -r["total_profit"])
        return rows

    def band(sale: dict[str, Any]) -> str:
        cost = sale["cost"]
        for label, lo, hi in PRICE_BANDS:
            if cost >= lo and (hi is None or cost <= hi):
                return label
        return "未分類"

    return {
        "by_category": aggregate(lambda s: s.get("category")),
        "by_source": aggregate(lambda s: s.get("purchase_source")),
        "by_price_band": aggregate(band),
        "by_channel": aggregate(lambda s: s.get("channel")),
        "grade_distribution": _grade_distribution(sales),
    }


def _grade_distribution(sales: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for s in sales:
        counts[s["grade"]] = counts.get(s["grade"], 0) + 1
    return [
        {"label": grade, "count": counts.get(grade, 0)}
        for grade in ("S", "A", "B", "C", "D")
        if counts.get(grade)
    ]
