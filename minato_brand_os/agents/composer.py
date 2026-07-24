from __future__ import annotations

"""Composer Agent — Proof & Personality Engine の中核。

朝便: ProofFact → Proof / Decision / Learning の投稿候補3件
夜便: メモ素材 → Personality の投稿候補3件（素材が無い日は質問を返す）

生成ルール（CEO指示）:
    数字だけ出さない。必ず「根拠・理由・判断・学び」をセットにする。
    ただし"判断"はみなとのブランドの核なので、テンプレ生成では
    穴埋め欄（→の行）として残し、人間が一言で埋める設計にする。
    Claudeキーがあれば判断案まで含めた全文を生成する。
"""

from typing import Any

from ..db import BrandDB, jst_iso
from ..llm import MODEL_ANALYZE, has_llm
from ..proof import collect_all_facts
from ..proof.base import ProofFact

MEMO_KINDS = {"fail": "失敗", "learn": "学び", "story": "実話", "thought": "考え方"}

# 素材ゼロの日に夜便で送る質問（1行答えるだけで明日の素材になる）
MEMO_QUESTIONS = [
    "今日いちばん「しまった」と思ったことは？（1行でOK）",
    "今日、数字より嬉しかった出来事は？",
    "今日やめたこと・やらないと決めたことは？",
]


# ---------------- 朝便: Proof / Decision / Learning ----------------

def _proof_template(fact: ProofFact) -> dict[str, str]:
    nums = " / ".join(f"{k} {v:,}" if isinstance(v, int) else f"{k} {v}" for k, v in fact.numbers.items())
    body = (
        f"{fact.headline}\n"
        f"\n"
        f"📊 根拠: {nums}\n"
        f"（{fact.context}）\n"
        f"\n"
        f"🧠 判断: → {fact.judgement_hint}\n"
        f"\n"
        f"📝 学び: → この数字から得た教訓を1行で"
    )
    return {"post_type": "proof", "title": fact.headline, "body": body, "source": fact.source}


def _decision_template(fact: ProofFact) -> dict[str, str]:
    body = (
        f"【判断の記録】{fact.headline}\n"
        f"\n"
        f"この状況で私は「→ 買う/売る/待つ」を選んだ。\n"
        f"理由: → 3つ書く（数字・経験・リスク）\n"
        f"\n"
        f"外れたら外れたで、それも公開します。\n"
        f"判断を晒すのが一番の勉強法なので。"
    )
    return {"post_type": "decision", "title": f"判断: {fact.headline}", "body": body, "source": fact.source}


def _learning_template(fact: ProofFact) -> dict[str, str]:
    body = (
        f"今日の学び。\n"
        f"\n"
        f"{fact.headline}。\n"
        f"数字だけ見ると「→ 表面的な結論」に見えるけど、\n"
        f"実際は「→ 一段深い気づき」だった。\n"
        f"\n"
        f"相場は毎日見てる人にしか教えてくれないことがある。"
    )
    return {"post_type": "learning", "title": f"学び: {fact.headline}", "body": body, "source": fact.source}


def _morning_llm(facts: list[ProofFact], cfg: dict[str, Any]) -> list[dict[str, str]] | None:
    if not has_llm() or not facts:
        return None
    brand = cfg["brand"]
    fact_lines = "\n".join(
        f"- {f.headline}（根拠: {f.numbers} / {f.context}）" for f in facts[:5]
    )
    prompt = (
        f"あなたは『{brand['owner']}』本人としてXの投稿を書く。\n"
        f"価値観: {', '.join(brand['values'])}。煽らない。稼げる系の誇張をしない。\n"
        f"以下は自作の価格監視システムが記録した実データの事実:\n{fact_lines}\n\n"
        f"この事実から投稿を3本書く。構成は必ず「事実の数字→根拠→自分の判断→学び」。\n"
        f"1本目=Proof(実績・数字), 2本目=Decision(なぜそう判断したか), 3本目=Learning(学び)。\n"
        f"各140-200字、日本語。番号や見出しは付けず、本文だけを「---」区切りで返す。"
    )
    try:
        from ..llm import _client

        msg = _client().messages.create(
            model=MODEL_ANALYZE, max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        parts = [p.strip() for p in msg.content[0].text.split("---") if p.strip()]
        if len(parts) < 3:
            return None
        types = ["proof", "decision", "learning"]
        return [
            {"post_type": types[i], "title": facts[0].headline, "body": parts[i], "source": "claude"}
            for i in range(3)
        ]
    except Exception as exc:  # noqa: BLE001
        print(f"[composer] morning llm fallback: {exc}")
        return None


def compose_morning(db: BrandDB, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Proof候補3件を生成してpost_draftsに保存。"""
    facts = collect_all_facts()
    drafts = _morning_llm(facts, cfg)
    if drafts is None:
        drafts = []
        if facts:
            drafts.append(_proof_template(facts[0]))
            drafts.append(_decision_template(facts[min(1, len(facts) - 1)]))
            drafts.append(_learning_template(facts[min(2, len(facts) - 1)]))
        else:
            drafts.append({
                "post_type": "learning", "title": "相場静観日",
                "body": "今日は相場に大きな動きなし。\n\n"
                        "動かない日こそ差がつく。\n→ 静観日にやっている仕込み・観察を1つ書く",
                "source": "template",
            })
    return _save_drafts(db, "morning", drafts)


# ---------------- 夜便: Personality ----------------

def _personality_template(memo: dict[str, Any]) -> dict[str, str]:
    kind_label = MEMO_KINDS.get(memo["kind"], "実話")
    if memo["kind"] == "fail":
        body = (
            f"失敗談を晒します。\n\n{memo['text']}\n\n"
            f"→ なぜそうなったか1行\n→ 次どうするか1行\n\n"
            f"失敗を隠す人より、晒す人を信用してもらえる方でいたい。"
        )
    elif memo["kind"] == "learn":
        body = (
            f"今日の学び。\n\n{memo['text']}\n\n"
            f"→ これに気づいたきっかけを1行\n\n"
            f"小さい気づきを言語化するのが一番の複利。"
        )
    else:
        body = (
            f"{memo['text']}\n\n"
            f"→ その時どう感じたか1行\n→ 何を大事にしたいと思ったか1行"
        )
    return {"post_type": "personality", "title": f"{kind_label}: {memo['text'][:20]}", "body": body, "source": "memo"}


def _night_llm(memos: list[dict[str, Any]], cfg: dict[str, Any]) -> list[dict[str, str]] | None:
    if not has_llm() or not memos:
        return None
    brand = cfg["brand"]
    memo_lines = "\n".join(f"- [{MEMO_KINDS.get(m['kind'],'実話')}] {m['text']}" for m in memos[:3])
    prompt = (
        f"あなたは『{brand['owner']}』本人としてXの投稿を書く。\n"
        f"価値観: {', '.join(brand['values'])}。等身大で、カッコつけない。\n"
        f"以下は本人の今日のメモ:\n{memo_lines}\n\n"
        f"このメモを元に、人柄が伝わる投稿を3本。失敗は美化せず、学びで締める。\n"
        f"各100-180字、日本語。本文だけを「---」区切りで返す。"
    )
    try:
        from ..llm import _client

        msg = _client().messages.create(
            model=MODEL_ANALYZE, max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        parts = [p.strip() for p in msg.content[0].text.split("---") if p.strip()]
        return [
            {"post_type": "personality", "title": memos[0]["text"][:20], "body": p, "source": "claude"}
            for p in parts[:3]
        ] or None
    except Exception as exc:  # noqa: BLE001
        print(f"[composer] night llm fallback: {exc}")
        return None


def _import_memo_csv(db: BrandDB) -> None:
    """data/memos.csv（iPhoneのGitHub webから編集可）を取り込む。列: kind,text。既出textはスキップ。"""
    import csv

    from ..config import BASE_DIR

    path = BASE_DIR / "data" / "memos.csv"
    if not path.exists():
        return
    with db.connect() as conn:
        existing = {r["text"] for r in conn.execute("SELECT text FROM memos").fetchall()}
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            for row in csv.DictReader(f):
                text = (row.get("text") or "").strip()
                kind = (row.get("kind") or "story").strip()
                if text and text not in existing and kind in MEMO_KINDS:
                    conn.execute(
                        "INSERT INTO memos (created_at, kind, text) VALUES (?,?,?)",
                        (jst_iso(), kind, text),
                    )
                    existing.add(text)


def compose_night(db: BrandDB, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Personality候補3件（素材が無ければ質問モード）。"""
    _import_memo_csv(db)
    with db.connect() as conn:
        memos = [dict(r) for r in conn.execute(
            "SELECT * FROM memos WHERE used=0 ORDER BY created_at DESC LIMIT 3"
        ).fetchall()]
    if not memos:
        drafts = [{
            "post_type": "personality", "title": f"質問{i+1}",
            "body": f"（素材メモが空です。下の質問に1行答えると、明日から投稿候補になります）\n\nQ. {q}",
            "source": "question",
        } for i, q in enumerate(MEMO_QUESTIONS)]
        return _save_drafts(db, "night", drafts)

    drafts = _night_llm(memos, cfg)
    if drafts is None:
        drafts = [_personality_template(m) for m in memos]
        while len(drafts) < 3:
            i = len(drafts) - len(memos)
            drafts.append({
                "post_type": "personality", "title": f"質問{i+1}",
                "body": f"Q. {MEMO_QUESTIONS[i % len(MEMO_QUESTIONS)]}（1行答えると明日の素材になります）",
                "source": "question",
            })
    with db.connect() as conn:
        for m in memos:
            conn.execute("UPDATE memos SET used=1 WHERE id=?", (m["id"],))
    return _save_drafts(db, "night", drafts)


def _save_drafts(db: BrandDB, slot: str, drafts: list[dict[str, str]]) -> list[dict[str, Any]]:
    saved = []
    with db.connect() as conn:
        for d in drafts[:3]:
            conn.execute(
                """INSERT INTO post_drafts (created_at, slot, post_type, title, body, source)
                   VALUES (?,?,?,?,?,?)""",
                (jst_iso(), slot, d["post_type"], d.get("title", ""), d["body"], d.get("source", "template")),
            )
            d_id = int(conn.execute("SELECT last_insert_rowid() i").fetchone()["i"])
            saved.append({"id": d_id, **d})
    db.log_run(f"compose_{slot}", f"drafts={len(saved)}")
    return saved
