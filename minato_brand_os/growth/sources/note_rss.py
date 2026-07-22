from __future__ import annotations

"""SourceAdapter: note 公式RSS。

CEO承認条件5:
    - 公式RSS内の情報だけを使う（プロフィールページ等の外部取得はしない）
    - 取得元URL・取得日時はdiscoveries台帳に保存（Deduplicator側で記録）
    - Xリンクが無い人物も note上の見込み顧客(medium='note') として保存する

noteの公式RSS:
    ユーザー単位:   https://note.com/<username>/rss
    マガジン単位:   https://note.com/<username>/m/<magazine>/rss
config(growth.sources.note_rss.feeds)にフィードURLを列挙して使う。
マガジンRSSの場合は記事リンクから複数の書き手を発掘できる。
"""

import re
from typing import Any

from ..schema import RawCandidate
from . import _rss

USER_RE = re.compile(r"note\.com/([A-Za-z0-9_]+)")


def _candidates_from_feed(feed: _rss.Feed, feed_url: str) -> list[RawCandidate]:
    out: dict[str, RawCandidate] = {}
    channel_author = (feed.title or "").replace("の新着記事", "").strip()
    for e in feed.entries:
        m = USER_RE.search(e["link"])
        if not m:
            continue
        username = m.group(1)
        if username in ("hashtag", "magazines", "info"):
            continue
        cand = out.get(username)
        title = e["title"]
        if cand is None:
            out[username] = RawCandidate(
                medium="note",
                handle=username,
                source_url=feed_url,
                name=e["author"] or channel_author or username,
                bio=f"note記事: {title}",
                genre="note",
                url=f"https://note.com/{username}",
            )
        elif title and len(cand.bio or "") < 200:
            cand.bio = f"{cand.bio} / {title}"
    return list(out.values())


def discover(cfg: dict[str, Any]) -> list[RawCandidate]:
    feeds = (cfg.get("growth", {}).get("sources", {}).get("note_rss", {}) or {}).get("feeds", [])
    out: list[RawCandidate] = []
    for feed_url in feeds:
        try:
            out.extend(_candidates_from_feed(_rss.parse(_rss.fetch(feed_url)), feed_url))
        except Exception as exc:  # noqa: BLE001 — 1フィード失敗で他を止めない
            print(f"[note_rss] {feed_url} 取得失敗: {exc}")
    return out
