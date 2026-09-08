from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


TITLE_LABELS = {
    "pokemon": "ポケカ",
    "onepiece": "ワンピ",
    "fusionworld": "FW",
}

STRATEGY_LABELS = {
    "short": "短期",
    "mid": "中期",
    "long": "長期",
}

CHANNEL_LABELS = {
    "mercari": "メルカリ",
    "yahoo": "ヤフオク",
    "kaitori": "買取店",
    "wholesale": "業者間卸",
}


@dataclass(frozen=True)
class Product:
    """監視対象の商品（シングル／BOX／プロモ／鑑定品）。data/products.csv の1行。"""

    product_id: str
    title: str                      # pokemon / onepiece / fusionworld
    name: str
    set_code: str = ""
    card_no: str = ""
    rarity: str = ""
    form: str = "single"            # single / box / promo / graded
    release_date: date | None = None
    search_keyword: str = ""        # 楽天API等の検索語（空なら name を使う）
    must_keywords: tuple[str, ...] = ()     # 商品名に全て含まれていること
    exclude_keywords: tuple[str, ...] = ()  # 1つでも含まれていたら除外
    surugaya_url: str = ""          # 駿河屋 販売ページ（or 検索URL）
    surugaya_kaitori_url: str = ""  # 駿河屋 買取ページ
    supply_status: str = "unknown"  # active / discontinued / unknown
    graded: bool = False
    active: bool = True

    @property
    def title_label(self) -> str:
        return TITLE_LABELS.get(self.title, self.title)

    @property
    def display_no(self) -> str:
        return " ".join(part for part in (self.set_code, self.card_no) if part)

    @property
    def keyword(self) -> str:
        return self.search_keyword or self.name

    def matches(self, text: str) -> bool:
        """検索結果の商品名が、このカードを指しているかの簡易判定。"""
        lowered = text.lower()
        if any(word.lower() in lowered for word in self.exclude_keywords):
            return False
        return all(word.lower() in lowered for word in self.must_keywords)


@dataclass
class Observation:
    """1回の価格観測。side='buy' は自分が仕入れられる価格、'sell' は自分が売れる価格。"""

    product_id: str
    source: str
    side: str                       # buy / sell
    channel: str                    # buy側: rakuten/surugaya… sell側: kaitori/mercari/yahoo
    price: int
    url: str
    condition: str = ""
    stock: int | None = None
    sold_count_30d: int | None = None   # フリマ相場シートの直近売れ数（流動性）
    raw_text: str = ""
    observed_at: str | None = None


@dataclass
class Metrics:
    """1商品の当日指標（metrics.py が算出）。"""

    product_id: str
    as_of: date
    buy_min: int | None = None
    buy_source: str = ""
    buy_url: str = ""
    buy_condition: str = ""
    stock_total: int | None = None
    listing_count: int = 0
    sell_prices: dict[str, int] = field(default_factory=dict)  # channel -> 想定売価
    sold_count_30d: int | None = None
    chg_7d: float | None = None
    chg_30d: float | None = None
    chg_90d: float | None = None
    volatility: float | None = None
    history_days: int = 0
    liquidity: float = 0.0          # 0..1

    def to_row(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "product_id": self.product_id,
            "buy_min": self.buy_min,
            "buy_source": self.buy_source,
            "stock_total": self.stock_total,
            "listing_count": self.listing_count,
            "sell_kaitori": self.sell_prices.get("kaitori"),
            "sell_mercari": self.sell_prices.get("mercari"),
            "sell_yahoo": self.sell_prices.get("yahoo"),
            "chg_7d": self.chg_7d,
            "chg_30d": self.chg_30d,
            "chg_90d": self.chg_90d,
            "volatility": self.volatility,
            "history_days": self.history_days,
            "liquidity": self.liquidity,
        }


@dataclass
class ChannelResult:
    """出口チャネルごとの手取り計算結果。"""

    channel: str
    gross: int              # 想定売価
    fee: int
    shipping: int
    packing: int
    net: int                # 手取り
    profit: int             # 手取り − 仕入
    margin: float           # profit / buy
    turnover_days: int

    @property
    def label(self) -> str:
        return CHANNEL_LABELS.get(self.channel, self.channel)


@dataclass
class ProfitResult:
    buy_price: int
    channels: list[ChannelResult]
    best: ChannelResult
    flea_limit_price: int | None    # この額以下で仕入れれば目標利益率を確保


@dataclass
class Candidate:
    """戦略スコアリング後の候補1件。"""

    product: Product
    metrics: Metrics
    profit: ProfitResult
    strategy: str
    base_score: float
    risk_factor: float
    reasons: list[str]
    risks: list[str]
    recommended_qty: int = 1

    @property
    def score(self) -> float:
        return self.base_score * self.risk_factor
