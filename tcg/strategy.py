from __future__ import annotations

from datetime import date
from typing import Any

from .models import Candidate, Metrics, Product, ProfitResult


STRATEGY_ORDER = ["short", "mid", "long"]


def clip(value: float | None, scale: float) -> float:
    """value/scale を 0..1 に丸める。None は 0。"""
    if value is None or scale <= 0:
        return 0.0
    return max(0.0, min(1.0, value / scale))


def scarcity_score(stock_total: int | None) -> float:
    if stock_total is None:
        return 0.2      # 在庫数が取れないソース（楽天等）は「不明」として弱く加点
    if stock_total <= 0:
        return 0.0
    if stock_total <= 2:
        return 1.0
    if stock_total <= 5:
        return 0.5
    return 0.0


def supply_score(product: Product) -> float:
    return {"discontinued": 1.0, "unknown": 0.4}.get(product.supply_status, 0.0)


# ---------------------------------------------------------------- リスク補正
def risk_factor(product: Product, buy_price: int, metrics: Metrics, config: dict[str, Any]) -> tuple[float, list[str]]:
    """仕入上限を設けない代わりに、高額品ほどスコアを割り引く。"""
    risk = config.get("risk", {})
    threshold = int(risk.get("high_value_threshold", 100000))
    risks: list[str] = []
    liquidity_factor = 1.0
    authenticity_factor = 1.0
    if buy_price >= threshold:
        # 売れ行きが薄い高額品ほど売れ残りリスク
        liquidity_factor = 0.5 + 0.5 * metrics.liquidity
        if not product.graded and product.form != "box":
            authenticity_factor = float(risk.get("ungraded_high_value_factor", 0.7))
            risks.append("未鑑定の高額品：真贋・状態の確認必須")
        else:
            risks.append("高額品：売れ残り時の資金拘束に注意")
    halflife = float(risk.get("capital_halflife_yen", 300000))
    capital_factor = 1.0 / (1.0 + buy_price / halflife)
    return round(liquidity_factor * authenticity_factor * capital_factor, 4), risks


# ---------------------------------------------------------------- 各戦略
def evaluate_short(product: Product, m: Metrics, p: ProfitResult, cfg: dict[str, Any]) -> tuple[float, list[str]] | None:
    best = p.best
    if best.margin < float(cfg.get("min_margin", 0.15)):
        return None
    if best.profit < int(cfg.get("min_profit", 1500)):
        return None
    if best.turnover_days > int(cfg.get("max_turnover_days", 14)):
        return None
    if m.stock_total is not None and m.stock_total <= 0:
        return None
    reasons: list[str] = []
    kaitori = m.sell_prices.get("kaitori")
    if kaitori and kaitori >= p.buy_price:
        reasons.append(f"買取上限 {kaitori:,}円 ≥ 仕入 {p.buy_price:,}円（下値が堅い）")
    surge = float(cfg.get("surge_7d", 0.08))
    if m.chg_7d is not None and m.chg_7d >= surge:
        reasons.append(f"7日騰落 {m.chg_7d:+.1%}（急騰）")
    if m.stock_total is not None and 0 < m.stock_total <= 5:
        reasons.append(f"在庫 {m.stock_total}点まで減少")
    if m.sold_count_30d:
        reasons.append(f"直近30日 売れ {m.sold_count_30d}件")
    reasons.append(f"{best.label}で純利益 {best.profit:+,}円（{best.margin:.1%}）")
    score = (
        0.40 * clip(best.margin, 0.5)
        + 0.30 * m.liquidity
        + 0.20 * clip(m.chg_7d, 0.2)
        + 0.10 * scarcity_score(m.stock_total)
    )
    return round(score, 4), reasons


def evaluate_mid(product: Product, m: Metrics, p: ProfitResult, cfg: dict[str, Any], as_of: date) -> tuple[float, list[str]] | None:
    best = p.best
    if best.margin < float(cfg.get("min_margin", 0.25)):
        return None
    if m.history_days < int(cfg.get("min_history_days", 30)):
        return None
    lo, hi = cfg.get("release_window_days", [30, 90])
    if product.release_date is None:
        return None
    age = (as_of - product.release_date).days
    if not (int(lo) <= age <= int(hi)):
        return None
    if m.chg_30d is None:
        return None
    # 底打ち反転：30日で下落 → 直近7日で上向き
    if m.chg_30d < 0 and (m.chg_7d or 0) > 0:
        reversal = 1.0
    elif (m.chg_7d or 0) > 0:
        reversal = 0.5
    else:
        reversal = 0.0
    reasons = [
        f"発売後 {age}日（開封集中期の底値圏）",
        f"30日騰落 {m.chg_30d:+.1%} ／ 7日騰落 {(m.chg_7d or 0):+.1%}",
        f"{best.label}で純利益 {best.profit:+,}円（{best.margin:.1%}）",
    ]
    if reversal == 1.0:
        reasons.append("下落から反転のシグナル")
    score = (
        0.35 * clip(best.margin, 0.6)
        + 0.25 * reversal
        + 0.20 * m.liquidity
        + 0.20 * clip(-m.chg_30d, 0.3)
    )
    return round(score, 4), reasons


def evaluate_long(product: Product, m: Metrics, p: ProfitResult, cfg: dict[str, Any]) -> tuple[float, list[str]] | None:
    best = p.best
    if best.margin < float(cfg.get("min_margin", 0.40)):
        return None
    if m.history_days < int(cfg.get("min_history_days", 90)):
        return None
    if m.chg_90d is None or m.chg_90d < 0:
        return None
    max_vol = float(cfg.get("max_volatility", 0.15))
    if m.volatility is not None and m.volatility > max_vol:
        return None
    reasons = [
        f"90日騰落 {m.chg_90d:+.1%}（長期右肩上がり）",
        f"{best.label}で純利益 {best.profit:+,}円（{best.margin:.1%}）",
    ]
    if product.supply_status == "discontinued":
        reasons.append("供給停止（絶版・再販終了）")
    if product.graded:
        reasons.append("鑑定済み（真贋リスク低）")
    if m.volatility is not None:
        reasons.append(f"30日ボラ {m.volatility:.1%}（安定）")
    vol_score = 1.0 - clip(m.volatility, max_vol) if m.volatility is not None else 0.5
    score = (
        0.30 * clip(best.margin, 0.8)
        + 0.30 * clip(m.chg_90d, 0.3)
        + 0.20 * supply_score(product)
        + 0.20 * vol_score
    )
    return round(score, 4), reasons


def general_risks(product: Product, m: Metrics, p: ProfitResult, strategy: str, as_of: date) -> list[str]:
    risks: list[str] = []
    if strategy == "short":
        risks.append("値動きが速いため14日以内に売り切る")
    if product.release_date and (as_of - product.release_date).days < 90 and product.form != "box":
        risks.append("再録・再販の可能性（発売90日未満）")
    if m.sold_count_30d is not None and m.sold_count_30d < 3:
        risks.append("直近の売れ数が少ない（売れ残りリスク）")
    if "kaitori" not in m.sell_prices:
        risks.append("買取価格の裏付けなし（フリマ相場のみ）")
    if p.best.channel in {"mercari", "yahoo"}:
        risks.append("フリマ相場は手動更新値。出品前に最新相場を再確認")
    return risks


def recommended_qty(strategy: str, m: Metrics) -> int:
    if strategy == "short":
        qty = max(1, round(m.liquidity * 3))
    elif strategy == "mid":
        qty = 2 if m.liquidity >= 0.5 else 1
    else:
        qty = 1
    if m.stock_total is not None and m.stock_total > 0:
        qty = min(qty, m.stock_total)
    return qty


def evaluate(product: Product, m: Metrics, p: ProfitResult, config: dict[str, Any], as_of: date) -> list[Candidate]:
    """商品1件を3戦略で評価し、条件を満たした戦略ぶんの Candidate を返す。"""
    strategies = config.get("strategies", {})
    factor, risk_notes = risk_factor(product, p.buy_price, m, config)
    candidates: list[Candidate] = []
    results = {
        "short": evaluate_short(product, m, p, strategies.get("short", {})),
        "mid": evaluate_mid(product, m, p, strategies.get("mid", {}), as_of),
        "long": evaluate_long(product, m, p, strategies.get("long", {})),
    }
    for strategy, result in results.items():
        if result is None:
            continue
        score, reasons = result
        candidates.append(
            Candidate(
                product=product,
                metrics=m,
                profit=p,
                strategy=strategy,
                base_score=score,
                risk_factor=factor,
                reasons=reasons,
                risks=general_risks(product, m, p, strategy, as_of) + risk_notes,
                recommended_qty=recommended_qty(strategy, m),
            )
        )
    return candidates


# ---------------------------------------------------------------- 選定
def select_daily(
    candidates: list[Candidate],
    config: dict[str, Any],
    excluded_product_ids: set[str],
) -> dict[str, list[Candidate]]:
    """各戦略の上位N件を選ぶ。制約：同一商品は1日1回／クールダウン除外／
    タイトルごとの最低件数／高額品は1日あたり上限。足りない戦略は水増ししない。"""
    output = config.get("output", {})
    per_strategy: dict[str, int] = output.get("per_strategy", {"short": 5, "mid": 5, "long": 5})
    titles_cfg: dict[str, dict[str, Any]] = config.get("titles", {})
    risk = config.get("risk", {})
    high_threshold = int(risk.get("high_value_threshold", 100000))
    high_max = int(risk.get("high_value_max_per_day", 3))

    pool = [
        c for c in candidates
        if c.product.product_id not in excluded_product_ids
        and titles_cfg.get(c.product.title, {}).get("enabled", True)
    ]
    used: set[str] = set()
    high_used = 0
    selected: dict[str, list[Candidate]] = {s: [] for s in STRATEGY_ORDER}

    def can_take(c: Candidate) -> bool:
        nonlocal high_used
        if c.product.product_id in used:
            return False
        if c.profit.buy_price >= high_threshold and high_used >= high_max:
            return False
        return True

    def take(strategy: str, c: Candidate) -> None:
        nonlocal high_used
        selected[strategy].append(c)
        used.add(c.product.product_id)
        if c.profit.buy_price >= high_threshold:
            high_used += 1

    for strategy in STRATEGY_ORDER:
        limit = int(per_strategy.get(strategy, 0))
        if limit <= 0:
            continue
        ranked = sorted((c for c in pool if c.strategy == strategy), key=lambda c: c.score, reverse=True)
        # 1) タイトルごとの最低件数を先に確保
        for title, tcfg in titles_cfg.items():
            need = int(tcfg.get("daily_min", 0))
            for c in ranked:
                if need <= 0 or len(selected[strategy]) >= limit:
                    break
                if c.product.title == title and can_take(c):
                    take(strategy, c)
                    need -= 1
        # 2) 残りをスコア順で埋める
        for c in ranked:
            if len(selected[strategy]) >= limit:
                break
            if can_take(c):
                take(strategy, c)
        selected[strategy].sort(key=lambda c: c.score, reverse=True)
    return selected
