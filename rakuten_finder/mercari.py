"""メルカリ相場ソース。

メルカリには公式の検索 API が無く、スクレイピングは利用規約違反のリスクが
高いため、MVP では相場データを CSV（手入力/半手動）で投入する。

`MercariSource` を差し替え可能なインターフェースとして定義しておき、
将来、正規の手段（提携 API 等）が使えるようになったら実装を追加する。

CSV フォーマット（data/mercari_prices.csv）:
    query,sold_median,sold_min,sold_avg,sold_count,active_count,active_min,stability,note
    4902370536485,7200,6500,7150,25,8,6980,0.9,ゼルダ ティアキン
    "Anker PowerCore 10000",3200,2800,3150,40,15,2980,0.8,

- query には JAN コード or 検索キーワードを入れる。
- 楽天商品との照合は JAN 完全一致 → 商品名の部分一致 の順で行う。
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Protocol

from .models import MercariStats, RakutenItem


class MercariSource(Protocol):
    """相場ソースのインターフェース（将来差し替え可能）。"""

    def lookup(self, item: RakutenItem) -> MercariStats | None:
        """商品に対応する相場を返す。見つからなければ None。"""
        ...


class CsvMercariSource:
    """CSV ファイルから相場を読む MVP 実装。"""

    def __init__(self, csv_path: str | Path) -> None:
        self.csv_path = Path(csv_path)
        self._stats: list[MercariStats] = []
        self._by_jan: dict[str, MercariStats] = {}
        self._load()

    def _load(self) -> None:
        if not self.csv_path.exists():
            return
        with self.csv_path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                stats = _row_to_stats(row)
                if stats is None:
                    continue
                self._stats.append(stats)
                if stats.query.isdigit() and len(stats.query) in (8, 13):
                    self._by_jan[stats.query] = stats

    def lookup(self, item: RakutenItem) -> MercariStats | None:
        # 1) JAN 完全一致（最も信頼できる紐付け）
        if item.jan and item.jan in self._by_jan:
            return self._by_jan[item.jan]
        # 2) クエリ文字列が商品名に含まれる（部分一致）。最長一致を優先。
        name_lower = item.name.lower()
        matched = [
            s for s in self._stats
            if not s.query.isdigit() and s.query.lower() in name_lower
        ]
        if matched:
            return max(matched, key=lambda s: len(s.query))
        return None

    def __len__(self) -> int:
        return len(self._stats)


def _row_to_stats(row: dict[str, str]) -> MercariStats | None:
    query = (row.get("query") or "").strip()
    try:
        sold_median = int(float(row.get("sold_median") or 0))
    except ValueError:
        return None
    if not query or sold_median <= 0:
        return None

    def _num(key: str, default: float = 0) -> float:
        try:
            return float(row.get(key) or default)
        except ValueError:
            return default

    return MercariStats(
        query=query,
        sold_median=sold_median,
        sold_min=int(_num("sold_min")),
        sold_avg=_num("sold_avg"),
        sold_count=int(_num("sold_count")),
        active_count=int(_num("active_count")),
        active_min=int(_num("active_min")),
        stability=min(max(_num("stability", 1.0), 0.0), 1.0),
        note=(row.get("note") or "").strip(),
    )
