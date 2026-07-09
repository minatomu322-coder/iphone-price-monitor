from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import rakuten_finder.dashboard as dashboard
from rakuten_finder.database import FinderDatabase
from rakuten_finder.models import Candidate
from rakuten_finder.profit import calc_profit
from rakuten_finder.scoring import score_candidate


@pytest.fixture
def client(tmp_path: Path, item, stats, assumptions, weights, thresholds, monkeypatch):
    db = FinderDatabase(tmp_path / "dash.sqlite3")
    profit = calc_profit(item, stats, assumptions)
    score = score_candidate(item, stats, profit, weights, thresholds)
    db.save_candidate(Candidate(item=item, stats=stats, profit=profit, score=score))
    monkeypatch.setattr(dashboard, "_db", db)
    return TestClient(dashboard.app)


def test_index_lists_items(client, item):
    response = client.get("/")
    assert response.status_code == 200
    assert "利益商品AI" in response.text
    assert item.shop_name in response.text


def test_api_items(client, item):
    response = client.get("/api/items")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["item_code"] == item.item_code


def test_decide_records_decision(client, item):
    response = client.post(
        "/decide",
        data={"item_code": item.item_code, "decision": "buy"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    data = client.get("/api/items").json()
    assert data[0]["decision"] == "buy"


def test_rank_filter(client):
    response = client.get("/?rank=D")
    assert response.status_code == 200
