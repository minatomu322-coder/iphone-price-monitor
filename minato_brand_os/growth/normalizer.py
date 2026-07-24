from __future__ import annotations

"""Normalizer — RawCandidate を候補DB(accounts)の形へ正規化する。

- handleの整形（@除去・空白除去）
- 媒体別のURL補完
- 保存キーの決定: medium='x' は handle そのまま、他媒体は "handle#medium"
  （accounts.handle はUNIQUEのため、異なる媒体の同名handleの衝突を防ぐ。
    例: note の taro は 'taro#note'。X の taro とは別人として保存される）
"""

from .schema import RawCandidate

MEDIUM_URL = {
    "x": "https://x.com/{}",
    "note": "https://note.com/{}",
}


def storage_key(medium: str, handle: str) -> str:
    return handle if medium == "x" else f"{handle}#{medium}"


def normalize(cand: RawCandidate) -> dict | None:
    handle = (cand.handle or "").strip().lstrip("@")
    if not handle:
        return None
    medium = (cand.medium or "x").strip().lower()
    url = cand.url or MEDIUM_URL.get(medium, "").format(handle) or cand.source_url
    acc = {
        "handle": storage_key(medium, handle),
        "medium": medium,
        "url": url,
        "name": (cand.name or "").strip() or None,
        "bio": (cand.bio or "").strip() or None,
        "genre": (cand.genre or "").strip() or None,
        "source": None,  # Deduplicatorがsource名を入れる
    }
    if isinstance(cand.followers, int):
        acc["followers"] = cand.followers
    return acc
