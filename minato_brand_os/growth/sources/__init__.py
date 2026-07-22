"""SourceAdapter レジストリ。

SourceAdapter = 実際に RawCandidate を返すものだけ（CEO承認条件1）。
人間向けの巡回支援(reference_patrol)はアダプタではなく通知側の機能。

新しい媒体の追加手順:
    1. このパッケージにモジュールを作り discover(cfg) -> list[RawCandidate] を実装
    2. 下の REGISTRY に1行追加
    3. config.mbos.yaml の growth.sources に enabled を追加
将来: web_search(Claude API) / youtube(Data API) / x_api / instagram / threads
"""

from __future__ import annotations

from typing import Any, Callable

from ..schema import RawCandidate
from . import note_rss, seed_csv

REGISTRY: dict[str, Callable[[dict[str, Any]], list[RawCandidate]]] = {
    "seed_csv": seed_csv.discover,
    "note_rss": note_rss.discover,
}
