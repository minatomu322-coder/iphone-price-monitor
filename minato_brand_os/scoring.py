from __future__ import annotations

"""ヒューリスティック採点エンジン（Claudeキー無しでも動く）。

数値(フォロワー/フォロー/エンゲージ)とテキスト(bio/直近投稿)のキーワード一致から
9軸を0-100で採点し、重み付き合計→★に変換する。

人柄・価値観のような"文脈理解が要る軸"は、キー無しでは中立値を置き confidence を下げる。
ANTHROPIC_API_KEY を投入すると llm.py がこれらを高精度採点で上書きする。
"""

from typing import Any


def _sweet_spot(value: int | None, low: int, high: int) -> float:
    """スイートスポット内で最大(100)。外れるほど滑らかに減衰。大きすぎ・小さすぎを両方減点。"""
    if value is None:
        return 50.0  # 不明は中立
    if low <= value <= high:
        return 100.0
    if value < low:
        return max(20.0, 100.0 * value / low)
    # high超過: 対数的に減衰（10倍で0付近）
    over = value / high
    return max(10.0, 100.0 - 40.0 * (over - 1))


def _keyword_score(text: str, targets: list[str], negatives: list[str]) -> tuple[float, list[str]]:
    text_l = (text or "").lower()
    hits = [k for k in targets if k.lower() in text_l]
    neg = [k for k in negatives if k.lower() in text_l]
    base = min(100.0, len(hits) * 34.0)          # 3語一致で満点
    base -= len(neg) * 40.0                        # 地雷ワードは大幅減点
    return max(0.0, base), hits


def score_account(acc: dict[str, Any], cfg: dict[str, Any],
                  growth: float | None = None) -> dict[str, Any]:
    sc = cfg["scoring"]
    kw = cfg["keywords"]
    low, high = sc["followers_sweet_spot"]
    text = f"{acc.get('bio','')} {acc.get('recent_posts','')}"

    brand_fit, hits = _keyword_score(text, kw["target"], kw.get("negative", []))
    values_fit, _ = _keyword_score(acc.get("bio", ""), cfg["brand"]["values"], [])

    followers = acc.get("followers")
    following = acc.get("following")
    followers_axis = _sweet_spot(followers, low, high)

    # 絡みやすさ: フォロー数が多い / フォロー>フォロワー気味なアカは返信しやすい傾向
    if followers and following:
        ratio = following / max(followers, 1)
        reply_rate = min(100.0, 40.0 + ratio * 60.0)
    else:
        reply_rate = 50.0

    engagement = acc.get("engagement")
    engagement_axis = min(100.0, engagement * 20.0) if engagement is not None else 50.0

    if growth is not None:
        growth_axis = max(0.0, min(100.0, 50.0 + growth * 500.0))  # +10%成長で満点付近
    else:
        growth_axis = 50.0

    interaction_value = round(0.5 * followers_axis + 0.5 * reply_rate, 1)
    # コンサル見込み: ブランド相性×関与度を近似（キー投入で精緻化）
    consult_ltv = round(0.6 * brand_fit + 0.4 * engagement_axis, 1)

    axes = {
        "brand_fit": round(brand_fit, 1),
        "consult_ltv": consult_ltv,
        "interaction_value": interaction_value,
        "engagement": round(engagement_axis, 1),
        "growth": round(growth_axis, 1),
        "personality": 50.0,   # 文脈理解が必要 → キー無しでは中立
        "values_fit": round(values_fit, 1),
        "reply_rate": round(reply_rate, 1),
        "followers": round(followers_axis, 1),
    }

    weights = sc["weights"]
    wsum = sum(weights.values())
    total = sum(axes[a] * weights.get(a, 0) for a in axes) / wsum

    total = round(total, 1)
    star = to_star(total, sc["star_thresholds"])

    # confidence: 数値・テキストがどれだけ揃っているか
    have = sum(x is not None for x in (followers, following, engagement)) / 3
    text_rich = min(1.0, len(text.strip()) / 80)
    confidence = round(0.3 + 0.4 * have + 0.3 * text_rich, 2)

    reason = _reason(star, hits, followers, axes)
    return {"star": star, "total_score": total, "axes": axes,
            "reason": reason, "engine": "heuristic", "confidence": confidence}


def to_star(total: float, thresholds: list[int]) -> int:
    t5, t4, t3, t2 = thresholds
    if total >= t5:
        return 5
    if total >= t4:
        return 4
    if total >= t3:
        return 3
    if total >= t2:
        return 2
    return 1


def _reason(star: int, hits: list[str], followers: int | None, axes: dict[str, float]) -> str:
    parts: list[str] = []
    if hits:
        parts.append("関連ジャンル: " + "・".join(hits[:4]))
    if followers is not None:
        parts.append(f"フォロワー{followers:,}")
    if axes["consult_ltv"] >= 70:
        parts.append("コンサル見込み高")
    if axes["reply_rate"] >= 70:
        parts.append("絡みやすい")
    if not parts:
        parts.append("情報不足（シードに bio/投稿を足すと精度UP）")
    return " / ".join(parts)
