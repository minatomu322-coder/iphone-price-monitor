"""FastAPI ダッシュボード。

起動:
    uvicorn rakuten_finder.dashboard:app --reload --port 8000

機能:
- GET  /               : 利益商品一覧（最新観測を score 順に表示）
- POST /decide         : 買う / 見送り / 保留 を記録（学習に反映）
- GET  /api/items      : 一覧の JSON API
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .config import load_config
from .database import FinderDatabase

app = FastAPI(title="利益商品AI ダッシュボード")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_config = None
_db: FinderDatabase | None = None


def get_db() -> FinderDatabase:
    """設定と DB を遅延初期化（テストで差し替えやすくする）。"""
    global _config, _db
    if _db is None:
        _config = load_config()
        _db = FinderDatabase(_config.db_path)
    return _db


@app.get("/", response_class=HTMLResponse)
def index(request: Request, rank: str = "", decision: str = "") -> HTMLResponse:
    rows = get_db().latest_observations(limit=300)
    if rank:
        rows = [r for r in rows if r["rank"] == rank]
    if decision:
        if decision == "none":
            rows = [r for r in rows if not r["decision"]]
        else:
            rows = [r for r in rows if r["decision"] == decision]
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "rows": rows,
            "rank_filter": rank,
            "decision_filter": decision,
        },
    )


@app.post("/decide")
def decide(item_code: str = Form(...), decision: str = Form(...), note: str = Form("")) -> RedirectResponse:
    get_db().record_decision(item_code, decision, note)
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/items")
def api_items(limit: int = 300) -> list[dict]:
    return get_db().latest_observations(limit=limit)
