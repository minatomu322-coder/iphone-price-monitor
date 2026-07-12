"""Proofソースのレジストリ。

新しいソース（メルカリ・ポケカ・利益商品）を追加する手順:
    1. このパッケージに モジュールを作り、collect_facts() -> list[ProofFact] を実装
    2. 下の SOURCES に1行追加
それだけで朝便の候補生成に自動的に組み込まれる。
"""

from __future__ import annotations

from typing import Callable

from .base import ProofFact
from . import iphone_price

# ソース名 → 収集関数。ここに1行足すだけで拡張できる。
SOURCES: dict[str, Callable[[], list[ProofFact]]] = {
    "iphone_price": iphone_price.collect_facts,
    # "mercari": mercari.collect_facts,       # 将来: メルカリ売却実績
    # "pokeca": pokeca.collect_facts,         # 将来: ポケカ相場・売買
}


def collect_all_facts() -> list[ProofFact]:
    facts: list[ProofFact] = []
    for name, fn in SOURCES.items():
        try:
            facts.extend(fn())
        except Exception as exc:  # noqa: BLE001 — 1ソースの失敗で朝便全体を止めない
            print(f"[proof] source '{name}' failed: {exc}")
    # インパクト（動きの大きさ）順
    facts.sort(key=lambda f: abs(f.impact), reverse=True)
    return facts
