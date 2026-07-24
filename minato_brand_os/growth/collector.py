from __future__ import annotations

"""Collector — 有効な全SourceAdapterを実行し、エラーを隔離する。

1ソースの障害で全体を止めない（CEOテスト項目5）。
収集数に制限は設けない（収集と通知の分離）。
"""

from typing import Any

from .schema import RawCandidate
from .sources import REGISTRY


def collect(cfg: dict[str, Any], registry: dict | None = None) -> tuple[list[tuple[str, RawCandidate]], dict[str, str]]:
    """返り値: ([(source名, RawCandidate), ...], {source名: エラーメッセージ})"""
    registry = registry if registry is not None else REGISTRY
    sources_cfg = cfg.get("growth", {}).get("sources", {})
    results: list[tuple[str, RawCandidate]] = []
    errors: dict[str, str] = {}
    for name, discover in registry.items():
        if not (sources_cfg.get(name, {}) or {}).get("enabled", True):
            continue
        try:
            for cand in discover(cfg):
                results.append((name, cand))
        except Exception as exc:  # noqa: BLE001 — 隔離して次のソースへ
            errors[name] = str(exc)[:200]
            print(f"[collector] source '{name}' failed (isolated): {exc}")
    return results, errors
