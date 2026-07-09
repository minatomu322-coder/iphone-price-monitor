from __future__ import annotations

import dataclasses
from pathlib import Path

from rakuten_finder.mercari import CsvMercariSource

CSV = """query,sold_median,sold_min,sold_avg,sold_count,active_count,active_min,stability,note
4902370550733,36500,34000,36200,25,6,35800,0.9,JAN一致
Anker PowerCore,3200,2800,3150,40,15,2980,0.8,
Anker PowerCore 10000,3500,3000,3400,30,10,3300,0.8,より長い一致
"""


def make_source(tmp_path: Path) -> CsvMercariSource:
    csv_path = tmp_path / "mercari.csv"
    csv_path.write_text(CSV, encoding="utf-8")
    return CsvMercariSource(csv_path)


def test_lookup_by_jan(tmp_path, item):
    source = make_source(tmp_path)
    stats = source.lookup(item)
    assert stats is not None
    assert stats.query == "4902370550733"
    assert stats.sold_median == 36500


def test_lookup_by_keyword_longest_match(tmp_path, item):
    source = make_source(tmp_path)
    other = dataclasses.replace(
        item, name="Anker PowerCore 10000 モバイルバッテリー", jan=None
    )
    stats = source.lookup(other)
    assert stats is not None
    assert stats.query == "Anker PowerCore 10000"  # 最長一致を優先


def test_lookup_no_match(tmp_path, item):
    source = make_source(tmp_path)
    other = dataclasses.replace(item, name="関係ない商品", jan=None)
    assert source.lookup(other) is None


def test_missing_csv_returns_empty(tmp_path, item):
    source = CsvMercariSource(tmp_path / "not_exist.csv")
    assert len(source) == 0
    assert source.lookup(item) is None
