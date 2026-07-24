from __future__ import annotations

"""Proofソース共通の事実(Fact)形式。

Fact = 「投稿の材料になる、数字付きの出来事」。
Composer がこれを Proof / Decision / Learning の投稿候補に変換する。
数字だけでなく、根拠と判断のヒントを必ず持たせる。
"""

from dataclasses import dataclass, field


@dataclass
class ProofFact:
    source: str            # iphone_price / mercari / pokeca ...
    kind: str              # price_move / profit_now / streak / spread ...
    headline: str          # 1行の事実。例:「iPhone 17 Pro 1TBの買取が3日で+6,000円」
    numbers: dict          # 根拠となる数字（価格・差分・期間・原価・利益など）
    context: str = ""      # 背景（店舗名・期間・条件）
    judgement_hint: str = ""  # 「私ならこう判断する」の下書きヒント
    impact: float = 0.0    # 動きの大きさ（候補の並び順に使う）
    tags: list[str] = field(default_factory=list)
