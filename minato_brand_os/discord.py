from __future__ import annotations

"""Discord通知（Embed / iPhone最適化）。

3便:
    noon(12:00)    今日いいねする100人 + 🔥最重要人物
    evening(17:30) 進捗リマインド + 追加候補
    night(22:00)   リプ5-10人 + コピペ用リプ文 + フォロー/DM推奨

webhookが未設定なら標準出力にプレビュー（ローカル確認用）。
"""

import os
from typing import Any

import requests

STAR = lambda n: "★" * n + "☆" * (5 - n)  # noqa: E731
X_URL = "https://x.com/{}"


def _post(webhook: str | None, payload: dict[str, Any]) -> None:
    if not webhook:
        print("=== Discord preview ===")
        for e in payload.get("embeds", []):
            print(f"# {e.get('title','')}")
            print(e.get("description", ""))
            for f in e.get("fields", []):
                print(f"[{f['name']}]\n{f['value']}")
        return
    r = requests.post(webhook, json=payload, timeout=20)
    r.raise_for_status()


def _chunk(text: str, limit: int = 1000) -> list[str]:
    """Discordのfield値上限に合わせて分割。"""
    out, buf = [], ""
    for line in text.splitlines(keepends=True):
        if len(buf) + len(line) > limit:
            out.append(buf)
            buf = ""
        buf += line
    if buf:
        out.append(buf)
    return out or [""]


def _like_lines(likes: list[dict[str, Any]]) -> str:
    lines = []
    for i, r in enumerate(likes, 1):
        lines.append(f"{i}. {STAR(r['star'])} {r['name']}  {X_URL.format(r['handle'])}")
    return "\n".join(lines)


def webhook_url() -> str | None:
    return os.environ.get("MBOS_DISCORD_WEBHOOK_URL") or os.environ.get("DISCORD_WEBHOOK_URL")


TYPE_LABEL = {"proof": "📊 Proof（実績）", "decision": "🧠 Decision（判断）",
              "personality": "🫶 Personality（人柄）", "learning": "📝 Learning（学び）"}


def _draft_fields(drafts: list[dict[str, Any]]) -> list[dict[str, str]]:
    fields = []
    for d in drafts:
        label = TYPE_LABEL.get(d["post_type"], d["post_type"])
        fields.append({
            "name": f"{label} ｜ 候補 #{d['id']}",
            "value": d["body"][:1000] + f"\n\n`投稿したら: python mbos.py posted --draft {d['id']}`",
        })
    return fields


def _engage_field(cand: dict[str, Any], n: int = 5) -> dict[str, str]:
    rows = cand["likes"][:n]
    value = "\n".join(f"{STAR(r['star'])} {r['name']}  {X_URL.format(r['handle'])}" for r in rows) or "候補なし"
    return {"name": "📣 投稿後に絡むと伸びる人（いいね/リプ）", "value": value}


def notify_morning(drafts: list[dict[str, Any]], cand: dict[str, Any], webhook: str | None = None) -> None:
    """朝便: Proof候補3件（実データ）＋投稿後の交流先。"""
    webhook = webhook or webhook_url()
    fields = _draft_fields(drafts)
    fields.append(_engage_field(cand))
    embed = {
        "title": "🌅 MINATO Brand OS ｜ 朝便（Proof）",
        "description": "自作システムの実データから生成。→の行を自分の言葉で埋めれば投稿完成。\n"
                       "**数字は機械が保証する。判断はあなたのブランド。**",
        "color": 0x10B981,
        "fields": fields[:25],
    }
    _post(webhook, {"embeds": [embed]})


def notify_night_personality(drafts: list[dict[str, Any]], cand: dict[str, Any], webhook: str | None = None) -> None:
    """夜便: Personality候補3件＋投稿後の交流先。"""
    webhook = webhook or webhook_url()
    fields = _draft_fields(drafts)
    fields.append(_engage_field(cand))
    embed = {
        "title": "🌃 MINATO Brand OS ｜ 夜便（Personality）",
        "description": "人柄・失敗・学びの投稿候補。等身大が一番強い。\n"
                       "素材が切れたら1行メモ: `python mbos.py memo --kind fail --text \"...\"`",
        "color": 0xEC4899,
        "fields": fields[:25],
    }
    _post(webhook, {"embeds": [embed]})


def notify_noon(cand: dict[str, Any], webhook: str | None = None) -> None:
    webhook = webhook or webhook_url()
    likes = cand["likes"]
    top = cand["top"]
    fields = []
    if top:
        fields.append({
            "name": "🔥 今日の最重要人物",
            "value": f"{STAR(top['star'])} **{top['name']}**  {X_URL.format(top['handle'])}\n理由: {top['reason']}",
        })
    # 100人リストは複数fieldに分割
    for idx, part in enumerate(_chunk(_like_lines(likes))):
        fields.append({"name": f"👍 今日いいねする人（{len(likes)}）" if idx == 0 else "　", "value": part or "候補なし"})
    embed = {
        "title": "🌞 MINATO Brand OS ｜ 12:00 便",
        "description": "AIが選んだ今日の交流候補。深く狭く、数より1本の神リプを。",
        "color": 0xF59E0B,
        "fields": fields[:25],
    }
    _post(webhook, {"embeds": [embed]})


def notify_evening(cand: dict[str, Any], progress: dict[str, int], webhook: str | None = None) -> None:
    webhook = webhook or webhook_url()
    done = progress.get("like", 0) + progress.get("reply", 0)
    extra = cand["likes"][: max(0, 20)]
    embed = {
        "title": "🌆 MINATO Brand OS ｜ 17:30 便",
        "description": f"今日の交流：**{done}件**（いいね{progress.get('like',0)} / リプ{progress.get('reply',0)}）\n"
                       f"夜のリプ({cand['reply_min']}〜{cand['reply_max']}件)に向けて、伸びてる人へ追いいいねを。",
        "color": 0x6366F1,
        "fields": [{"name": "追加で絡むと良い人", "value": _like_lines(extra[:15]) or "なし"}],
    }
    _post(webhook, {"embeds": [embed]})


def notify_night(cand: dict[str, Any], replies_text: dict[int, list[str]], webhook: str | None = None) -> None:
    webhook = webhook or webhook_url()
    fields = []
    for r in cand["replies"]:
        drafts = replies_text.get(r["id"], [])
        body = f"{X_URL.format(r['handle'])}\n" + "\n".join(f"┗ {d}" for d in drafts)
        fields.append({"name": f"{STAR(r['star'])} {r['name']} へのリプ案", "value": body[:1000]})
    if cand["follow"]:
        fields.append({"name": "＋フォロー推奨",
                       "value": "\n".join(f"{r['name']} {X_URL.format(r['handle'])}" for r in cand["follow"])})
    if cand["dm"]:
        fields.append({"name": "＋DM推奨（関係が温まった人）",
                       "value": "\n".join(f"{r['name']} {X_URL.format(r['handle'])}" for r in cand["dm"])})
    embed = {
        "title": "🌙 MINATO Brand OS ｜ 22:00 便",
        "description": f"今日リプする{cand['reply_min']}〜{cand['reply_max']}人。コピペOK、そのまま or 一言足して。",
        "color": 0x8B5CF6,
        "fields": fields[:25] or [{"name": "リプ候補", "value": "なし"}],
    }
    _post(webhook, {"embeds": [embed]})
