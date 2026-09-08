from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

import yaml

from tcg.database import TcgDatabase
from tcg.main import run
from tcg.models import Observation
from tcg.report import CSV_COLUMNS, split_message


ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 9, 8)


def write_config(tmp_path: Path) -> Path:
    with (ROOT / "tcg" / "config.yaml").open("r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    config["database"]["path"] = str(tmp_path / "db.sqlite3")
    config["catalog"]["products"] = str(tmp_path / "products.csv")
    config["output"]["csv_dir"] = str(tmp_path / "reports")
    config["flea_market"]["price_sheet"] = str(tmp_path / "sheet.csv")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return path


def test_run_no_fetch_writes_csv_and_records_submissions(tmp_path):
    config_path = write_config(tmp_path)
    (tmp_path / "products.csv").write_text(
        "product_id,title,name,set_code,card_no,rarity,form,release_date,search_keyword,must_keywords,exclude_keywords,surugaya_url,surugaya_kaitori_url,supply_status,graded,active\n"
        "p1,pokemon,カードA,SV9,1/100,SAR,single,2026-01-01,カードA SAR,カードA,,,,active,0,1\n"
        "p2,onepiece,カードB,OP99,OP99-001,SEC,single,2026-01-01,,,,,,active,0,1\n"
        "p3,fusionworld,BOX C,FB09,,BOX,box,2026-01-01,,,,,,active,0,0\n",
        encoding="utf-8",
    )
    (tmp_path / "sheet.csv").write_text(
        "product_id,channel,price_median,sold_count_30d,condition,updated_on\n"
        f"p1,mercari,16000,20,美品,{(AS_OF - timedelta(days=2)).isoformat()}\n",
        encoding="utf-8",
    )
    db = TcgDatabase(tmp_path / "db.sqlite3")
    db.insert_observations(
        [
            Observation("p1", "rakuten", "buy", "rakuten", 10000, "https://r/1"),
            Observation("p2", "rakuten", "buy", "rakuten", 5000, "https://r/2"),
            Observation("p1", "surugaya", "sell", "kaitori", 11000, "https://s/1"),
        ],
        AS_OF,
    )

    selected = run(config_path, as_of=AS_OF, fetch=False, dry_run=False)
    short_ids = [c.product.product_id for c in selected["short"]]
    assert short_ids == ["p1"]                       # p2 は出口価格が無いので候補外
    assert selected["mid"] == [] and selected["long"] == []

    csv_path = tmp_path / "reports" / f"{AS_OF.isoformat()}.csv"
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0].keys()) == CSV_COLUMNS
    assert rows[0]["戦略"] == "短期" and rows[0]["推奨出口"] == "メルカリ"
    assert rows[0]["フリマ指値"] and rows[0]["探索キーワード"] == "カードA SAR"

    # 提出履歴に記録され、翌日はクールダウンで除外される
    assert db.recently_submitted(AS_OF + timedelta(days=1), 14) == {"p1"}
    again = run(config_path, as_of=AS_OF + timedelta(days=1), fetch=False, dry_run=True)
    assert again["short"] == []


def test_split_message_respects_limit():
    content = "\n".join(f"line {i} " + "x" * 100 for i in range(60))
    chunks = split_message(content, limit=1900)
    assert len(chunks) > 1
    assert all(len(c) <= 1900 for c in chunks)
    assert "\n".join(chunks) == content
