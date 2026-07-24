from __future__ import annotations

"""Claude API ラッパー（任意）。

ANTHROPIC_API_KEY が未設定なら has_llm()=False を返し、システムはヒューリスティック採点で動く。
キーを投入すると、人柄・価値観・伸び代などの"文脈理解が要る軸"を Claude が高精度採点で上書きする。

依存 anthropic は任意。未インストールでも import は失敗しない（遅延import）。
"""

import json
import os
from typing import Any

MODEL_ANALYZE = os.environ.get("MBOS_MODEL_ANALYZE", "claude-opus-4-8")
MODEL_BULK = os.environ.get("MBOS_MODEL_BULK", "claude-haiku-4-5-20251001")


def has_llm() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _client() -> Any:
    from anthropic import Anthropic  # 遅延import: キー無し環境では触れない

    return Anthropic()


def _extract_json(text: str) -> dict[str, Any]:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"JSONが見つかりません: {text[:200]}")
    return json.loads(text[start : end + 1])


def score_account_llm(acc: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any] | None:
    """Claudeで多軸採点。失敗時はNone（呼び出し側がヒューリスティックにフォールバック）。"""
    if not has_llm():
        return None
    brand = cfg["brand"]
    axes = list(cfg["scoring"]["weights"].keys())
    axes_spec = ", ".join('"%s": <0-100>' % a for a in axes)
    prompt = (
        f"あなたは『{brand['owner']}』のブランド構築を支援するアナリストです。\n"
        f"ミッション: {brand['mission']}\n"
        f"価値観: {', '.join(brand['values'])}\n"
        f"目的はフォロワー増加ではなくブランド構築（信頼・仲間・コンサル見込み客）。\n\n"
        f"次のXアカウントを、みなとが交流する価値の観点で採点してください。\n"
        f"handle: @{acc.get('handle')}\n"
        f"name: {acc.get('name')}\n"
        f"bio: {acc.get('bio')}\n"
        f"followers: {acc.get('followers')} / following: {acc.get('following')}\n"
        f"直近投稿: {acc.get('recent_posts')}\n\n"
        f"各軸を0-100で採点し、必ず次のJSONだけを返す:\n"
        '{"axes": {' + axes_spec + '}, '
        '"reason": "<みなとが読む交流すべき理由。40字以内>", "confidence": <0-1>}'
    )
    try:
        client = _client()
        msg = client.messages.create(
            model=MODEL_BULK,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        data = _extract_json(msg.content[0].text)
    except Exception as exc:  # noqa: BLE001 — LLM障害時は静かにフォールバック
        print(f"[llm] score fallback: {exc}")
        return None

    axes_out = {a: float(data["axes"].get(a, 50)) for a in axes}
    weights = cfg["scoring"]["weights"]
    wsum = sum(weights.values())
    total = round(sum(axes_out[a] * weights.get(a, 0) for a in axes_out) / wsum, 1)
    from .scoring import to_star

    return {
        "star": to_star(total, cfg["scoring"]["star_thresholds"]),
        "total_score": total,
        "axes": axes_out,
        "reason": data.get("reason", ""),
        "engine": "claude",
        "confidence": float(data.get("confidence", 0.7)),
    }
