"""データモデル定義。

楽天から取得した商品（RakutenItem）、メルカリ相場（MercariStats）、
利益計算結果（ProfitResult）、スコアリング結果（ScoreResult）を扱う。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# JAN コード（8桁 or 13桁）。商品名・キャッチコピーから拾えることがある。
JAN_RE = re.compile(r"\b(\d{13}|\d{8})\b")


@dataclass(frozen=True)
class RakutenItem:
    """楽天API から取得した商品 1 件。"""

    item_code: str            # 例: "shop:10001234"（楽天全体で一意）
    name: str
    price: int                # 税込価格
    url: str
    shop_name: str
    keyword: str              # どの検索キーワードでヒットしたか
    shipping_included: bool   # 送料込みか（postageFlag==0）
    point_rate: float         # 商品ポイント倍率（shopPointRate 含む）
    in_stock: bool
    image_url: str | None = None
    genre_id: str | None = None
    jan: str | None = None
    catchcopy: str = ""
    review_count: int = 0
    review_average: float = 0.0

    @staticmethod
    def extract_jan(*texts: str) -> str | None:
        """商品名などのテキストから JAN らしき数字列を拾う。"""
        for text in texts:
            match = JAN_RE.search(text or "")
            if match:
                return match.group(1)
        return None


@dataclass(frozen=True)
class MercariStats:
    """メルカリ相場（MVP では CSV / 手入力で投入する）。"""

    query: str                    # 照合キー（JAN またはキーワード）
    sold_median: int              # 売り切れの中央値
    sold_min: int = 0             # 売り切れの最安値
    sold_avg: float = 0.0         # 売り切れの平均
    sold_count: int = 0           # 直近売れた件数（回転率の目安）
    active_count: int = 0         # 出品中の件数（少ないほど競合が薄い）
    active_min: int = 0           # 出品中の最安値
    stability: float = 1.0        # 相場の安定性 0..1（1=安定）。0.5未満は警告
    note: str = ""

    @property
    def unstable(self) -> bool:
        return self.stability < 0.5

    def sell_price(self, mode: str = "median") -> int:
        """販売想定価格。median（既定）/ min / avg を選べる。"""
        if mode == "min" and self.sold_min > 0:
            return self.sold_min
        if mode == "avg" and self.sold_avg > 0:
            return int(self.sold_avg)
        return self.sold_median


@dataclass(frozen=True)
class ProfitResult:
    """利益計算の結果。"""

    rakuten_price: int
    shipping_in: int          # 仕入送料（送料込みなら 0）
    coupon: int               # クーポン値引き額（円）
    point_total: int          # 想定ポイント合計（円換算）
    effective_cost: int       # 実質仕入価格
    sell_price: int           # メルカリ想定売価
    mercari_fee: int          # 手数料（既定 10%）
    shipping_out: int         # 発送想定送料
    profit: int               # 想定利益
    roi: float                # profit / effective_cost
    margin: float             # profit / sell_price


@dataclass(frozen=True)
class ScoreResult:
    """スコアリング結果。score は 0..100、rank は S/A/B/C/D。"""

    score: float
    rank: str
    breakdown: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Candidate:
    """パイプラインを流れる 1 商品分の評価済みデータ。"""

    item: RakutenItem
    stats: MercariStats
    profit: ProfitResult
    score: ScoreResult

    @property
    def mercari_search_url(self) -> str:
        from urllib.parse import quote

        return f"https://jp.mercari.com/search?keyword={quote(self.stats.query)}"
