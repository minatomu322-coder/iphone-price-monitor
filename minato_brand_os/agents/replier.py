from __future__ import annotations

"""Replier Agent — 22時だけ、コピペで使える自然なリプ案を作る。

Claudeキーがあれば相手の投稿に合わせた3案を生成。無ければ、宣伝臭ゼロの
"雛形3案"（相手の名前入り）を返す。人間がiPhoneでワンタップ調整して使える品質を狙う。
"""

from typing import Any

from ..llm import MODEL_ANALYZE, has_llm


def _fallback(cand: dict[str, Any]) -> list[str]:
    name = cand["name"]
    return [
        f"{name}さんの投稿、すごく共感しました。自分も同じこと感じてます…！",
        f"これ勉強になります {name}さん。実際にやってみてどうでしたか？",
        f"{name}さんの視点いいですね。続きも楽しみにしてます👀",
    ]


def generate_replies(cand: dict[str, Any], cfg: dict[str, Any]) -> list[str]:
    if not has_llm() or not cand.get("recent_posts"):
        return _fallback(cand)
    brand = cfg["brand"]
    prompt = (
        f"あなたは『{brand['owner']}』本人として、Xで自然なリプを書きます。\n"
        f"価値観: {', '.join(brand['values'])}（煽らない・宣伝しない・誠実に）。\n\n"
        f"相手: {cand['name']}（@{cand['handle']}）\n"
        f"相手の投稿: {cand['recent_posts']}\n\n"
        f"この投稿への自然な日本語リプを3案。各60字以内。宣伝・営業臭を絶対に出さない。"
        f"箇条書きで案だけを返す。"
    )
    try:
        from ..llm import _client

        msg = _client().messages.create(
            model=MODEL_ANALYZE,
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}],
        )
        lines = [l.strip(" -・*0123456789.") for l in msg.content[0].text.splitlines()]
        drafts = [l for l in lines if l]
        return drafts[:3] or _fallback(cand)
    except Exception as exc:  # noqa: BLE001
        print(f"[replier] fallback: {exc}")
        return _fallback(cand)
