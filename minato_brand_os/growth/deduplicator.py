from __future__ import annotations

"""Deduplicator — 重複排除・証拠ベースのクロス媒体統合・discoveries台帳記録。

重複判定: 保存キー(handle#medium)の完全一致のみ。
クロス媒体統合(CEO承認条件3):
    本人が自分のプロフィール/フィードに明記したリンク(self_links)がある場合のみ、
    リンク先媒体のアカウントへ統合する。
    表示名・プロフィールの類似だけでは絶対に統合しない。
    統合時は discoveries.evidence に証拠URLを残す。
"""

import re
from typing import Any

from ..db import BrandDB, jst_iso
from .normalizer import normalize, storage_key
from .schema import RawCandidate

X_LINK_RE = re.compile(r"(?:x|twitter)\.com/([A-Za-z0-9_]{1,15})")


def _evidence_merge_target(cand: RawCandidate) -> tuple[str, str] | None:
    """self_links から統合先(保存キー, 証拠URL)を返す。証拠が無ければNone。"""
    x_url = cand.self_links.get("x")
    if x_url:
        m = X_LINK_RE.search(x_url)
        if m:
            return storage_key("x", m.group(1)), x_url
    return None


def dedupe_and_store(db: BrandDB, found: list[tuple[str, RawCandidate]]) -> dict[str, Any]:
    """正規化→重複排除→保存し、全件をdiscoveries台帳へ記録する。"""
    existing = {r["handle"].lower() for r in db.all_accounts()}
    stats = {"new": 0, "duplicate": 0, "merged": 0, "by_source": {}}

    for source, cand in found:
        acc = normalize(cand)
        if acc is None:
            continue
        acc["source"] = source

        # 証拠ベースのクロス媒体統合: 本人明記リンクがある場合のみ統合先キーへ
        evidence = None
        merge = _evidence_merge_target(cand)
        if merge:
            target_key, evidence = merge
            acc["handle"] = target_key
            acc["medium"] = "x"
            acc["url"] = cand.self_links["x"]
            if target_key.lower() in existing:
                stats["merged"] += 1

        is_dup = acc["handle"].lower() in existing
        account_id = db.upsert_account(acc)  # 既存なら情報を補完更新（UPSERT）
        db.add_discovery(
            account_id=account_id, source=source,
            source_detail=cand.genre or cand.medium,
            source_url=cand.source_url, is_duplicate=is_dup,
            evidence=evidence,
        )
        src_stat = stats["by_source"].setdefault(source, {"new": 0, "duplicate": 0})
        if is_dup:
            stats["duplicate"] += 1
            src_stat["duplicate"] += 1
        else:
            stats["new"] += 1
            src_stat["new"] += 1
            existing.add(acc["handle"].lower())
    return stats
