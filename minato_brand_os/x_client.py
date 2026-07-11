from __future__ import annotations

"""収集元の抽象化レイヤ。

いま: 手動シードCSV(seed_csv)。0円・規約セーフ・今すぐ動く。
将来: X API予算が付いたら ApiSource を実装し、CLIの --source を切替えるだけで
      無停止で自動収集へ昇格できる。ここより上のコードは収集元を意識しない。

CSVフォーマット（列は最低限 handle だけあればよい。他は分かる範囲で）:
    handle,name,bio,followers,following,genre,recent_posts,engagement
"""

import csv
from pathlib import Path
from typing import Any, Iterable

from .config import SEEDS_DIR

INT_FIELDS = ("followers", "following")
FLOAT_FIELDS = ("engagement",)


def _clean(row: dict[str, str]) -> dict[str, Any] | None:
    handle = (row.get("handle") or "").strip().lstrip("@")
    if not handle:
        return None
    acc: dict[str, Any] = {"handle": handle, "source": "seed_csv"}
    for key in ("name", "bio", "genre", "recent_posts"):
        val = (row.get(key) or "").strip()
        if val:
            acc[key] = val
    for key in INT_FIELDS:
        raw = (row.get(key) or "").strip().replace(",", "")
        if raw.isdigit():
            acc[key] = int(raw)
    for key in FLOAT_FIELDS:
        raw = (row.get(key) or "").strip()
        try:
            if raw:
                acc[key] = float(raw)
        except ValueError:
            pass
    return acc


def load_seed_csv(path: str | Path) -> list[dict[str, Any]]:
    accounts: list[dict[str, Any]] = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            acc = _clean(row)
            if acc:
                accounts.append(acc)
    return accounts


def collect_seeds(seeds_dir: str | Path | None = None) -> list[dict[str, Any]]:
    """seeds/ 配下の全CSVを読み込み、handle重複は後勝ちでマージする。"""
    directory = Path(seeds_dir) if seeds_dir else SEEDS_DIR
    merged: dict[str, dict[str, Any]] = {}
    for csv_path in sorted(directory.glob("*.csv")):
        for acc in load_seed_csv(csv_path):
            merged.setdefault(acc["handle"], {}).update(acc)
    return list(merged.values())


def collect(source: str = "seed_csv", **kwargs: Any) -> list[dict[str, Any]]:
    if source == "seed_csv":
        return collect_seeds(kwargs.get("seeds_dir"))
    raise NotImplementedError(
        f"収集元 '{source}' は未実装です。現在は seed_csv のみ対応（X API は予算確保後にここへ追加）。"
    )
