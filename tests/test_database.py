from __future__ import annotations

from pathlib import Path

import pytest

from rakuten_finder.database import FinderDatabase
from rakuten_finder.models import Candidate
from rakuten_finder.profit import calc_profit
from rakuten_finder.scoring import score_candidate


@pytest.fixture
def db(tmp_path: Path) -> FinderDatabase:
    return FinderDatabase(tmp_path / "test.sqlite3")


@pytest.fixture
def candidate(item, stats, assumptions, weights, thresholds) -> Candidate:
    profit = calc_profit(item, stats, assumptions)
    score = score_candidate(item, stats, profit, weights, thresholds)
    return Candidate(item=item, stats=stats, profit=profit, score=score)


def test_save_and_read(db, candidate):
    obs_id = db.save_candidate(candidate)
    assert obs_id > 0
    rows = db.latest_observations()
    assert len(rows) == 1
    row = rows[0]
    assert row["item_code"] == candidate.item.item_code
    assert row["profit"] == candidate.profit.profit
    assert row["rank"] == candidate.score.rank
    assert row["decision"] is None
    assert row["notified"] == 0


def test_save_twice_keeps_latest(db, candidate):
    db.save_candidate(candidate, observed_at="2026-07-01T00:00:00+00:00")
    db.save_candidate(candidate, observed_at="2026-07-02T00:00:00+00:00")
    rows = db.latest_observations()
    assert len(rows) == 1  # 商品ごとに最新のみ
    assert rows[0]["observed_at"] == "2026-07-02T00:00:00+00:00"


def test_decision_recording_and_stats(db, candidate):
    db.save_candidate(candidate)
    db.record_decision(candidate.item.item_code, "buy", "良さそう")
    stats = db.decision_stats_by_keyword()
    assert stats[candidate.item.keyword]["buy"] == 1
    rows = db.latest_observations()
    assert rows[0]["decision"] == "buy"
    with pytest.raises(ValueError):
        db.record_decision("x", "invalid")


def test_notification_dedupe(db, candidate):
    key = "item|28200|36500"
    assert db.should_notify(key, candidate.item.item_code, 24) is True
    assert db.should_notify(key, candidate.item.item_code, 24) is False  # 24h以内は抑制
    assert db.should_notify("other-key", candidate.item.item_code, 24) is True


def test_daily_summary(db, candidate):
    db.save_candidate(candidate)
    summary = db.daily_summary("2000-01-01T00:00:00+00:00")
    assert summary["total"] == 1
    assert len(summary["top_profit"]) == 1
