from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from rakuten_finder.config import (
    Assumptions,
    Config,
    ScoringWeights,
    Target,
    Thresholds,
)
from rakuten_finder.database import FinderDatabase
from rakuten_finder.mercari import CsvMercariSource
from rakuten_finder.pipeline import run_pipeline


@pytest.fixture
def config(tmp_path: Path) -> Config:
    csv_path = tmp_path / "mercari.csv"
    csv_path.write_text(
        "query,sold_median,sold_min,sold_avg,sold_count,active_count,active_min,stability,note\n"
        "4902370550733,36500,34000,36200,25,6,35800,0.9,\n",
        encoding="utf-8",
    )
    return Config(
        app_id_env="TEST_RAKUTEN_APP_ID",
        affiliate_id_env="TEST_RAKUTEN_AFF_ID",
        webhook_env="TEST_WEBHOOK",
        db_path=tmp_path / "test.sqlite3",
        hits_per_keyword=30,
        request_delay_seconds=0,
        mercari_csv=csv_path,
        assumptions=Assumptions(spu_rate=5.0),
        thresholds=Thresholds(min_profit=1000, min_roi=0.10, notify_rank=("S", "A", "B")),
        weights=ScoringWeights(),
        targets=[Target(keyword="Nintendo Switch 有機EL")],
    )


def test_run_pipeline_end_to_end(config, item, monkeypatch):
    monkeypatch.delenv("TEST_WEBHOOK", raising=False)
    db = FinderDatabase(config.db_path)
    source = CsvMercariSource(config.mercari_csv)

    with patch("rakuten_finder.pipeline.search_items", return_value=[item]):
        result = run_pipeline(config, db=db, source=source, notify=False)

    assert result.searched == 1
    assert result.matched == 1
    assert result.saved == 1
    assert not result.errors
    rows = db.latest_observations()
    assert len(rows) == 1
    assert rows[0]["profit"] > 0


def test_run_pipeline_notify_dedupe(config, item, monkeypatch):
    """同一条件の2回目巡回では再通知しない。"""
    monkeypatch.delenv("TEST_WEBHOOK", raising=False)
    db = FinderDatabase(config.db_path)
    source = CsvMercariSource(config.mercari_csv)

    with patch("rakuten_finder.pipeline.search_items", return_value=[item]), patch(
        "rakuten_finder.pipeline.notify_candidate"
    ) as mock_notify:
        first = run_pipeline(config, db=db, source=source, notify=True)
        second = run_pipeline(config, db=db, source=source, notify=True)

    assert first.notified == 1
    assert second.notified == 0
    assert mock_notify.call_count == 1


def test_run_pipeline_search_error_recorded(config, monkeypatch):
    monkeypatch.delenv("TEST_WEBHOOK", raising=False)
    db = FinderDatabase(config.db_path)
    source = CsvMercariSource(config.mercari_csv)

    from rakuten_finder.rakuten_api import RakutenApiError

    with patch(
        "rakuten_finder.pipeline.search_items", side_effect=RakutenApiError("boom")
    ):
        result = run_pipeline(config, db=db, source=source, notify=False)

    assert result.saved == 0
    assert result.errors
