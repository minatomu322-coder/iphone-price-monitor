"""設定の読み込み（.env + YAML）。

- .env: シークレット（Discord Webhook / 楽天 App ID / DB パス）を管理。
- YAML: 検索対象・想定倍率・しきい値・スコア重みなどの運用パラメータ。

python-dotenv が無い環境でも動くよう、.env は自前の軽量パーサでも読める。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent


def load_dotenv(path: str | Path | None = None) -> None:
    """.env を環境変数へ読み込む（既存の環境変数は上書きしない）。"""
    try:
        from dotenv import load_dotenv as _load  # type: ignore

        _load(path or BASE_DIR / ".env", override=False)
        return
    except Exception:
        pass

    env_path = Path(path or BASE_DIR / ".env")
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class Assumptions:
    spu_rate: float = 5.0            # SPU の上乗せ想定(%)
    campaign_rate: float = 0.0       # 5と0の日 / 勝ったら倍などの想定(%)
    point_cap: int = 0               # ポイント上限(0=無制限)
    mercari_fee_rate: float = 0.10   # メルカリ手数料
    shipping_out: int = 200          # 転売時の想定送料
    default_shipping_in: int = 0     # 送料別の場合に仮定する仕入送料
    sell_percentile: str = "median"  # median | min | avg


@dataclass(frozen=True)
class Thresholds:
    min_profit: int = 1000
    min_roi: float = 0.10
    notify_rank: tuple[str, ...] = ("S", "A")
    alert_repeat_hours: int = 24


@dataclass(frozen=True)
class ScoringWeights:
    profit: float = 0.35
    roi: float = 0.25
    turnover: float = 0.20
    scarcity: float = 0.10
    stability: float = 0.10
    # 正規化の基準値（この値で 1.0 になる）
    profit_norm: int = 5000
    roi_norm: float = 0.30
    turnover_norm: int = 30


@dataclass(frozen=True)
class Target:
    keyword: str
    genre_id: str | None = None
    min_price: int | None = None
    max_price: int | None = None
    ng_keywords: list[str] = field(default_factory=list)
    hits: int | None = None


@dataclass(frozen=True)
class Config:
    app_id_env: str
    affiliate_id_env: str
    webhook_env: str
    db_path: Path
    hits_per_keyword: int
    request_delay_seconds: float
    mercari_csv: Path
    assumptions: Assumptions
    thresholds: Thresholds
    weights: ScoringWeights
    targets: list[Target]

    @property
    def app_id(self) -> str | None:
        return os.getenv(self.app_id_env)

    @property
    def affiliate_id(self) -> str | None:
        return os.getenv(self.affiliate_id_env)

    @property
    def webhook_url(self) -> str | None:
        return os.getenv(self.webhook_env)


def _resolve(path_str: str | Path) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else BASE_DIR / path


def load_config(path: str | Path | None = None) -> Config:
    load_dotenv()
    cfg_path = _resolve(path or BASE_DIR / "config" / "rakuten_finder.yaml")
    raw: dict[str, Any] = {}
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

    rk = raw.get("rakuten", {})
    asm = raw.get("assumptions", {})
    th = raw.get("thresholds", {})
    sc = raw.get("scoring", {}).get("weights", {})

    # DB パスは .env(RAKUTEN_FINDER_DB) > yaml > 既定 の順で解決。
    db_path = os.getenv("RAKUTEN_FINDER_DB") or raw.get("database", {}).get(
        "path", "data/rakuten_finder.sqlite3"
    )

    targets = [
        Target(
            keyword=str(t["keyword"]),
            genre_id=(str(t["genre_id"]) if t.get("genre_id") else None),
            min_price=t.get("min_price"),
            max_price=t.get("max_price"),
            ng_keywords=list(t.get("ng_keywords", [])),
            hits=t.get("hits"),
        )
        for t in raw.get("targets", [])
        if t.get("keyword")
    ]

    return Config(
        app_id_env=rk.get("app_id_env", "RAKUTEN_APP_ID"),
        affiliate_id_env=rk.get("affiliate_id_env", "RAKUTEN_AFFILIATE_ID"),
        webhook_env=raw.get("discord", {}).get("webhook_env", "DISCORD_WEBHOOK_URL"),
        db_path=_resolve(db_path),
        hits_per_keyword=int(rk.get("hits_per_keyword", 30)),
        request_delay_seconds=float(rk.get("request_delay_seconds", 1)),
        mercari_csv=_resolve(raw.get("mercari", {}).get("csv_path", "data/mercari_prices.csv")),
        assumptions=Assumptions(
            spu_rate=float(asm.get("spu_rate", 5)),
            campaign_rate=float(asm.get("campaign_rate", 0)),
            point_cap=int(asm.get("point_cap", 0)),
            mercari_fee_rate=float(asm.get("mercari_fee_rate", 0.10)),
            shipping_out=int(asm.get("shipping_out", 200)),
            default_shipping_in=int(asm.get("default_shipping_in", 0)),
            sell_percentile=str(asm.get("sell_percentile", "median")),
        ),
        thresholds=Thresholds(
            min_profit=int(th.get("min_profit", 1000)),
            min_roi=float(th.get("min_roi", 0.10)),
            notify_rank=tuple(th.get("notify_rank", ["S", "A"])),
            alert_repeat_hours=int(th.get("alert_repeat_hours", 24)),
        ),
        weights=ScoringWeights(
            profit=float(sc.get("profit", 0.35)),
            roi=float(sc.get("roi", 0.25)),
            turnover=float(sc.get("turnover", 0.20)),
            scarcity=float(sc.get("scarcity", 0.10)),
            stability=float(sc.get("stability", 0.10)),
            profit_norm=int(sc.get("profit_norm", 5000)),
            roi_norm=float(sc.get("roi_norm", 0.30)),
            turnover_norm=int(sc.get("turnover_norm", 30)),
        ),
        targets=targets,
    )
