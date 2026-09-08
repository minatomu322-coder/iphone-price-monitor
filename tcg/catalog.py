from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from .models import Product


COLUMNS = [
    "product_id", "title", "name", "set_code", "card_no", "rarity", "form",
    "release_date", "search_keyword", "must_keywords", "exclude_keywords",
    "surugaya_url", "surugaya_kaitori_url", "supply_status", "graded", "active",
]

VALID_TITLES = {"pokemon", "onepiece", "fusionworld"}
VALID_FORMS = {"single", "box", "promo", "graded"}


def _split(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split("|") if part.strip())


def _bool(value: str, default: bool) -> bool:
    value = (value or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "y"}


def _date(value: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    return date.fromisoformat(value)


def row_to_product(row: dict[str, str]) -> Product:
    title = row["title"].strip()
    if title not in VALID_TITLES:
        raise ValueError(f"未知のタイトル: {title} (product_id={row.get('product_id')})")
    form = (row.get("form") or "single").strip() or "single"
    if form not in VALID_FORMS:
        raise ValueError(f"未知のform: {form} (product_id={row.get('product_id')})")
    return Product(
        product_id=row["product_id"].strip(),
        title=title,
        name=row["name"].strip(),
        set_code=(row.get("set_code") or "").strip(),
        card_no=(row.get("card_no") or "").strip(),
        rarity=(row.get("rarity") or "").strip(),
        form=form,
        release_date=_date(row.get("release_date", "")),
        search_keyword=(row.get("search_keyword") or "").strip(),
        must_keywords=_split(row.get("must_keywords") or ""),
        exclude_keywords=_split(row.get("exclude_keywords") or ""),
        surugaya_url=(row.get("surugaya_url") or "").strip(),
        surugaya_kaitori_url=(row.get("surugaya_kaitori_url") or "").strip(),
        supply_status=(row.get("supply_status") or "unknown").strip() or "unknown",
        graded=_bool(row.get("graded", ""), False),
        active=_bool(row.get("active", ""), True),
    )


def load_products(path: str | Path, include_inactive: bool = False) -> list[Product]:
    """data/products.csv を読み込む。product_id の重複はエラー。"""
    products: list[Product] = []
    seen: set[str] = set()
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if not (row.get("product_id") or "").strip():
                continue
            product = row_to_product(row)
            if product.product_id in seen:
                raise ValueError(f"product_id が重複しています: {product.product_id}")
            seen.add(product.product_id)
            if product.active or include_inactive:
                products.append(product)
    return products
