from __future__ import annotations

import dataclasses

from rakuten_finder.profit import calc_profit
from rakuten_finder.scoring import learning_boost, rank_of, score_candidate


def test_rank_of_bounds():
    assert rank_of(85) == "S"
    assert rank_of(80) == "S"
    assert rank_of(70) == "A"
    assert rank_of(55) == "B"
    assert rank_of(40) == "C"
    assert rank_of(10) == "D"


def test_score_good_candidate(item, stats, assumptions, weights, thresholds):
    profit = calc_profit(item, stats, assumptions)
    score = score_candidate(item, stats, profit, weights, thresholds)
    # 利益4,450円 / ROI 15%超 / 回転25件 / 出品6件 / 安定0.9 → 上位ランク
    assert score.rank in ("S", "A", "B")
    assert score.score > 50
    assert set(score.breakdown) == {"profit", "roi", "turnover", "scarcity", "stability"}


def test_score_gated_when_below_thresholds(item, stats, assumptions, weights, thresholds):
    # 相場が仕入とほぼ同じ → 利益ほぼゼロ → ゲートで D に落ちる
    bad_stats = dataclasses.replace(stats, sold_median=29000, sold_min=28000)
    profit = calc_profit(item, bad_stats, assumptions)
    score = score_candidate(item, bad_stats, profit, weights, thresholds)
    assert score.rank == "D"
    assert any("しきい値未達" in w for w in score.warnings)


def test_score_warns_unstable_market(item, stats, assumptions, weights, thresholds):
    unstable = dataclasses.replace(stats, stability=0.3)
    profit = calc_profit(item, unstable, assumptions)
    score = score_candidate(item, unstable, profit, weights, thresholds)
    assert any("不安定" in w for w in score.warnings)


def test_learning_boost():
    stats = {"Nintendo Switch 有機EL": {"buy": 8, "skip": 2}}
    assert learning_boost("Nintendo Switch 有機EL", stats) == 6.0  # (0.8-0.2)*10
    assert learning_boost("未知キーワード", stats) == 0.0
    # 記録が1件だけでは学習を効かせない
    assert learning_boost("a", {"a": {"buy": 1}}) == 0.0


def test_learning_boost_applied_to_score(item, stats, assumptions, weights, thresholds):
    profit = calc_profit(item, stats, assumptions)
    base = score_candidate(item, stats, profit, weights, thresholds)
    boosted = score_candidate(
        item, stats, profit, weights, thresholds,
        decision_stats={item.keyword: {"buy": 10, "skip": 0}},
    )
    assert boosted.score > base.score
