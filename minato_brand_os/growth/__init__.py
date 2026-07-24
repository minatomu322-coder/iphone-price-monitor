from __future__ import annotations

"""Growth Engine — 見込み顧客の発掘パイプライン。

SourceAdapter群 → Collector → Normalizer → Deduplicator → Scoring → Notification
の6層。ScoringとNotificationは既存モジュール(scoring/select/discord)をそのまま使う。
収集数に制限なし。通知は select.py 側で最大30人（水増しなし）。
"""

from typing import Any

from ..db import BrandDB
from .collector import collect
from .deduplicator import dedupe_and_store


def run_pipeline(db: BrandDB, cfg: dict[str, Any], registry: dict | None = None) -> dict[str, Any]:
    """発見→正規化→重複排除→保存。返り値は収集統計（ダッシュボード用）。"""
    found, errors = collect(cfg, registry=registry)
    stats = dedupe_and_store(db, found)
    stats["source_errors"] = errors
    db.log_run("growth_pipeline",
               f"new={stats['new']} dup={stats['duplicate']} merged={stats['merged']} errors={list(errors)}")
    return stats
