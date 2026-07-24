from __future__ import annotations

"""Scout Agent — Web検索でX候補アカウントを自動発掘する。

CEO仕様:
    ⑥ Claude API無しでも全体は動作（その場合Scoutはスキップし、通知はDB内プールから選抜。
       不足はダッシュボードに正直に表示）。APIキー追加だけでAIモードに移行、コード変更不要。
    ⑦ 新規候補が目標数(30)に届くまで、検索テーマをラウンド式に拡張して発掘を続ける。
       既知handleとの重複は加算しない（水増し禁止）。

X APIもスクレイピングも使わない。Claude APIのweb_searchサーバーツールで
公開Web（まとめ記事・ランキング・Yahoo!リアルタイム検索・note/ブログ）から
実在確認できたアカウントのみを追加する。
"""

import json
import os
from typing import Any

from ..db import BrandDB
from ..llm import has_llm

MODEL_SCOUT = os.environ.get("MBOS_MODEL_SCOUT", "claude-opus-4-8")
TARGET_NEW = 30          # 1日の新規発掘目標（CEO仕様⑦）
PER_ROUND = 12           # 1ラウンドで要求する件数
MAX_ROUNDS = 6           # コスト上限（6ラウンドで届かなければ不足を正直に報告）

WEB_SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 8}

# ラウンドごとに検索対象を拡張するテーマ群（CEO仕様⑦: 検索対象・クエリの拡張）
ROUND_THEMES = [
    "ポケカ投資・ポケモンカード高騰・相場を発信する個人アカウント",
    "トレンドせどり・メルカリ物販の実績を発信する個人（会社員・主婦の副業）",
    "ワンピースカード・トレカ全般の相場やコレクションを発信する個人",
    "FIRE・資産形成・新NISAを発信する会社員アカウント（リベシティ界隈含む）",
    "AI活用×副業、業務効率化を発信する個人アカウント",
    "Yahoo!リアルタイム検索やnote・ブログ経由で見つかる、フォロワー数千規模の"
    "ポケカ/せどり実践者（まとめ記事に載らない小規模な発信者を優先）",
]


def _prompt(cfg: dict[str, Any], theme: str, known_handles: set[str], want: int) -> str:
    brand = cfg["brand"]
    known = " ".join(f"@{h}" for h in sorted(known_handles)[-80:]) or "（まだ無し）"
    return (
        f"あなたは『{brand['owner']}』のX(Twitter)ブランド構築を助けるリサーチャーです。\n"
        f"目的: {brand['mission']}\n\n"
        f"Web検索を使って、次のテーマで発信している実在のXアカウントを新しく{want}件探してください:\n"
        f"【{theme}】\n\n"
        f"条件:\n"
        f"- 検索結果で実在が確認できたアカウントのみ（ハンドルの推測・創作は絶対禁止）\n"
        f"- 企業公式・アカウント売買業者・情報商材の煽り系は除外\n"
        f"- 個人の発信者を優先。相互に絡める規模（数百〜数万フォロワー）だとなお良い\n"
        f"- 既知のアカウントは絶対に含めない: {known}\n\n"
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


def _run_round(client, cfg: dict[str, Any], theme: str, known: set[str], want: int) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "user", "content": _prompt(cfg, theme, known, want)}]
    response = None
    for _ in range(5):  # pause_turn(サーバ側検索ループの一時停止)を再開しながら完走
        response = client.messages.create(
            model=MODEL_SCOUT, max_tokens=4000,
            tools=[WEB_SEARCH_TOOL], messages=messages,
        )
        if response.stop_reason != "pause_turn":
            break
        messages = [messages[0], {"role": "assistant", "content": response.content}]
    text = "".join(b.text for b in response.content if b.type == "text")
    return _extract_json_array(text)


def scout(db: BrandDB, cfg: dict[str, Any], target_new: int | None = None) -> dict[str, int]:
    """新規候補が目標数に届くまでテーマを拡張しながら発掘。返り値: {found, added, rounds}。"""
    target = target_new or TARGET_NEW
    if not has_llm():
        print("[scout] ANTHROPIC_API_KEY未設定のためスキップ（通知はDB内プールから選抜されます）")
        return {"found": 0, "added": 0, "rounds": 0}

    from anthropic import Anthropic

    client = Anthropic()
    known = {r["handle"].lower() for r in db.all_accounts()}
    found_total, added, rounds = 0, 0, 0

    for theme in ROUND_THEMES[:MAX_ROUNDS]:
        if added >= target:
            break
        rounds += 1
        want = min(PER_ROUND, target - added)
        try:
            candidates = _run_round(client, cfg, theme, known, want)
        except Exception as exc:  # noqa: BLE001 — 1ラウンド失敗で全体を止めない
            print(f"[scout] round{rounds} failed: {exc}")
            continue
        found_total += len(candidates)
        for c in candidates:
            handle = str(c.get("handle", "")).strip().lstrip("@")
            if not handle or handle.lower() in known:
                continue  # 重複は加算しない（水増し禁止）
            acc = {
                "handle": handle,
                "name": c.get("name"),
                "bio": c.get("bio"),
                "genre": c.get("genre"),
                "source": "scout",
                "url": f"https://x.com/{handle}",
                "medium": "x",
            }
            if isinstance(c.get("followers"), int):
                acc["followers"] = c["followers"]
            db.upsert_account(acc)
            known.add(handle.lower())
            added += 1
            if added >= target:
                break

    db.log_run("scout", f"found={found_total} added={added} rounds={rounds} target={target}")
    print(f"[scout] {rounds}ラウンド実行 → 発掘{found_total}件 / 新規追加 {added}/{target}件")
    if added < target:
        print(f"[scout] ⚠️ 目標未達（不足{target - added}件）。水増しはせず、ダッシュボードに反映されます。")
    return {"found": found_total, "added": added, "rounds": rounds}
