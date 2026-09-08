from __future__ import annotations

import csv
from pathlib import Path

from ..models import Observation, Product
from .base import make_observed_on


class ManualSheetSource:
    """フリマ相場シート（data/flea_market_prices.csv）。人が週1で更新する sell 側の価格。

    列: product_id, channel(mercari/yahoo), price_median, sold_count_30d, condition, updated_on
    """

    name = "manual_sheet"

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._rows = self._load()

    def _load(self) -> dict[str, list[dict[str, str]]]:
        rows: dict[str, list[dict[str, str]]] = {}
        if not self.path.exists():
            return rows
        with self.path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                product_id = (row.get("product_id") or "").strip()
                if not product_id:
                    continue
                rows.setdefault(product_id, []).append(row)
        return rows

    def fetch(self, product: Product) -> list[Observation]:
        observations: list[Observation] = []
        for row in self._rows.get(product.product_id, []):
            try:
                price = int(str(row.get("price_median", "")).replace(",", ""))
            except ValueError:
                continue
            sold_raw = str(row.get("sold_count_30d", "")).strip()
            sold = int(sold_raw) if sold_raw.isdigit() else None
            updated_on = make_observed_on(row.get("updated_on"))
            observations.append(
                Observation(
                    product_id=product.product_id,
                    source="manual_sheet",
                    side="sell",
                    channel=(row.get("channel") or "mercari").strip(),
                    price=price,
                    url=str(self.path),
                    condition=(row.get("condition") or "").strip(),
                    sold_count_30d=sold,
                    observed_at=updated_on.isoformat() if updated_on else None,
                )
            )
        return observations
