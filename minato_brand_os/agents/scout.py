from __future__ import annotations

"""Scout Agent — Web検索でX候補アカウントを自動発掘する。

X APIもスクレイピングも使わず、Claude APIのweb_searchサーバーツールで
公開Web上の情報（まとめ記事・ランキング・検索結果のプロフィール）から
実在のXアカウントを発掘し、accountsテーブルへ source='scout' で追加する。

ANTHROPIC_API_KEY が無い環境ではスキップ（手動シード運用のまま動く）。
"""

import json
import os
from typing import Any

from ..db import BrandDB
from ..llm import has_llm

MODEL_SCOUT = os.environ.get("MBOS_MODEL_SCOUT", "claude-opus-4-8")
MAX_NEW_PER_RUN = 10  # 1回の発掘で追加する上限（採点コストと質のバランス）

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 8}


def _prompt(cfg: dict[str, Any], known_handles: list[str]) -> str:
    kw = "、".join(cfg["keywords"]["target"][:8])
    brand = cfg["brand"]
    known = " ".join(f"@{h}" for h in known_handles[-60:]) or "（まだ無し）"
    return (
        f"あなたは『{brand['owner']}』のX(Twitter)ブランド構築を助けるリサーチャーです。\n"
        f"目的: {brand['mission']}\n\n"
        f"Web検索を使って、次のジャンルで発信している実在のXアカウントを新しく{MAX_NEW_PER_RUN}件探してください:\n"
        f"{kw}\n\n"
        f"条件:\n"
        f"- 検索結果で実在が確認できたアカウントのみ（ハンドルを推測・創作しない）\n"
        f"- 企業公式・アカウント売買業者・情報商材の煽り系は除外\n"
        f"- 個人の発信者（会社員・副業・コレクター）を優先。相互に絡める規模だとなお良い\n"
        f"- 既知のアカウントは除外: {known}\n\n"
        f"最後に、必ず次のJSON配列だけを返してください（説明文は不要）:\n"
        f'[{{"handle": "xxx", "name": "表示名", "bio": "発信内容の要約", '
        f'"genre": "ポケカ/せどり等", "followers": 数値または null, "source_url": "根拠URL"}}]'
    )


def _extract_json_array(text: str) -> list[dict[str, Any]]:
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        data = json.loads(text[start : end + 1])
        return data if isinstance(data, list) else []
    except json.JSONDecodeError:
        return []


def scout(db: BrandDB, cfg: dict[str, Any]) -> dict[str, int]:
    """Web検索で候補を発掘してDBへ追加。返り値: {found, added}。"""
    if not has_llm():
        print("[scout] ANTHROPIC_API_KEY未設定のためスキップ（手動シード運用のまま）")
        return {"found": 0, "added": 0}

    from anthropic import Anthropic

    client = Anthropic()
    known = [r["handle"] for r in db.all_accounts()]
    messages: list[dict[str, Any]] = [{"role": "user", "content": _prompt(cfg, known)}]

    response = None
    for _ in range(5):  # pause_turn(サーバ側検索ループの一時停止)を再開しながら完走
        response = client.messages.create(
            model=MODEL_SCOUT,
            max_tokens=4000,
            tools=[WEB_SEARCH_TOOL],
            messages=messages,
        )
        if response.stop_reason != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": response.content}]

    text = "".join(b.text for b in response.content if b.type == "text")
    candidates = _extract_json_array(text)

    added = 0
    known_set = set(known)
    for c in candidates:
        handle = str(c.get("handle", "")).strip().lstrip("@")
        if not handle or handle.lower() in {k.lower() for k in known_set}:
            continue
        acc = {
            "handle": handle,
            "name": c.get("name"),
            "bio": c.get("bio"),
            "genre": c.get("genre"),
            "source": "scout",
        }
        if isinstance(c.get("followers"), int):
            acc["followers"] = c["followers"]
        db.upsert_account(acc)
        known_set.add(handle)
        added += 1
        if added >= MAX_NEW_PER_RUN:
            break

    db.log_run("scout", f"found={len(candidates)} added={added}")
    print(f"[scout] 発掘 {len(candidates)}件 → 新規追加 {added}件")
    return {"found": len(candidates), "added": added}
