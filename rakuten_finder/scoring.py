"""スコアリング（S/A/B/C/D）と学習ブースト。

各指標を 0..1 に正規化し、重み付き合計を 0..100 のスコアにする。

- profit:    利益額（profit_norm 円で 1.0）
- roi:       投資利益率（roi_norm で 1.0）
- turnover:  回転率 = 直近売れた件数（turnover_norm 件で 1.0）
- scarcity:  出品数の少なさ（0件=1.0、多いほど 0 に近づく）
- stability: 相場の安定性（CSV の stability をそのまま使用）

学習ブースト: decisions テーブル（買う/見送り/保留の記録）から
キーワード別の傾向を算出し、buy が多いキーワードは加点、
skip が多いキーワードは減点する（±10点）。

ゲート条件: min_profit / min_roi を満たさない商品は C 以上を付けない。
"""
from __future__ import annotations

from .config import ScoringWeights, Thresholds
from .models import MercariStats, ProfitResult, RakutenItem, ScoreResult

RANK_BOUNDS = [(80.0, "S"), (65.0, "A"), (50.0, "B"), (35.0, "C")]


def clamp01(value: float) -> float:
    return min(max(value, 0.0), 1.0)


def rank_of(score: float) -> str:
    for bound, rank in RANK_BOUNDS:
        if score >= bound:
            return rank
    return "D"


def learning_boost(keyword: str, decision_stats: dict[str, dict[str, int]]) -> float:
    """decisions の傾向からキーワード別の加点/減点（-10..+10点）を返す。

    decision_stats: {keyword: {"buy": n, "skip": n, "hold": n}}
    """
    stats = decision_stats.get(keyword)
    if not stats:
        return 0.0
    total = sum(stats.values())
    if total < 2:  # 記録が少なすぎる間は学習を効かせない
        return 0.0
    buy_ratio = stats.get("buy", 0) / total
    skip_ratio = stats.get("skip", 0) / total
    return round((buy_ratio - skip_ratio) * 10, 2)


def score_candidate(
    item: RakutenItem,
    stats: MercariStats,
    profit: ProfitResult,
    weights: ScoringWeights,
    thresholds: Thresholds,
    decision_stats: dict[str, dict[str, int]] | None = None,
) -> ScoreResult:
    breakdown = {
        "profit": clamp01(profit.profit / weights.profit_norm),
        "roi": clamp01(profit.roi / weights.roi_norm),
        "turnover": clamp01(stats.sold_count / weights.turnover_norm),
        # 出品数 0 件 = 競合なし = 1.0。10件で約0.5、30件でほぼ0。
        "scarcity": clamp01(1.0 - stats.active_count / 20.0),
        "stability": clamp01(stats.stability),
    }
    base = (
        breakdown["profit"] * weights.profit
        + breakdown["roi"] * weights.roi
        + breakdown["turnover"] * weights.turnover
        + breakdown["scarcity"] * weights.scarcity
        + breakdown["stability"] * weights.stability
    ) * 100

    boost = learning_boost(item.keyword, decision_stats or {})
    score = min(max(base + boost, 0.0), 100.0)

    warnings: list[str] = []
    if stats.unstable:
        warnings.append("相場が不安定（stability<0.5）")
    if stats.sold_count == 0:
        warnings.append("直近の売れた実績なし")
    if stats.active_count >= 20:
        warnings.append(f"出品数が多い（{stats.active_count}件）")
    if profit.effective_cost <= 0:
        warnings.append("実質仕入がマイナス（データ要確認）")
    if item.jan is None:
        warnings.append("JAN未取得（相場照合はキーワード一致）")

    # ゲート: 最低利益 / 最低ROI を満たさない商品は要注意側に倒す
    if profit.profit < thresholds.min_profit or profit.roi < thresholds.min_roi:
        score = min(score, 34.0)  # C未満（=D）に制限
        warnings.append(
            f"しきい値未達（利益{profit.profit:,}円 / ROI {profit.roi:.1%}）"
        )

    return ScoreResult(
        score=round(score, 1),
        rank=rank_of(score),
        breakdown={k: round(v, 3) for k, v in breakdown.items()},
        warnings=warnings,
    )
