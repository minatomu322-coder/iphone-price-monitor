from __future__ import annotations

"""Analyzer Agent — 候補者を多軸採点し★を付け、CRMの関係を再計算する。

Claudeキーがあれば Claude採点、無ければヒューリスティック採点に自動フォールバック。
どちらでも同じ形の結果を返すので、上位(通知)は採点エンジンを意識しない。
"""

from typing import Any

from ..db import BrandDB
from ..llm import score_account_llm
from ..scoring import score_account


def analyze_all(db: BrandDB, cfg: dict[str, Any]) -> dict[str, int]:
    accounts = db.all_accounts()
    engines = {"claude": 0, "heuristic": 0}
    rel = cfg["relationship"]
    for row in accounts:
        acc = dict(row)
        growth = db.follower_growth(acc["id"])
        result = score_account_llm(acc, cfg) or score_account(acc, cfg, growth=growth)
        db.save_score(acc["id"], result)
        db.recompute_relationship(
            acc["id"], rel["points"], rel["decay_per_day"], rel["cadence_days"]
        )
        engines[result["engine"]] = engines.get(result["engine"], 0) + 1
    detail = f"analyzed={len(accounts)} claude={engines['claude']} heuristic={engines['heuristic']}"
    db.log_run("analyze", detail)
    return {"total": len(accounts), **engines}
