from __future__ import annotations

"""SourceAdapter: 汎用ブログRSS（はてな・WordPress・Ameba等の公式フィード）。

feeds に公式RSS/AtomのURLを列挙すると、そのブログの書き手を
medium='blog' の見込み顧客として収集する（1フィード=1人）。
"""

from typing import Any
from urllib.parse import urlparse

from ..schema import RawCandidate
from . import _rss


def _handle_from(feed: _rss.Feed, feed_url: str) -> str:
    """ブログの一意なIDとしてドメイン（＋先頭パス）を使う。"""
    target = feed.link or feed_url
    p = urlparse(target)
    path = p.path.strip("/").split("/")[0]
    return f"{p.netloc}/{path}" if path else p.netloc


def discover(cfg: dict[str, Any]) -> list[RawCandidate]:
    feeds = (cfg.get("growth", {}).get("sources", {}).get("blog_rss", {}) or {}).get("feeds", [])
    out: list[RawCandidate] = []
    for feed_url in feeds:
        try:
            feed = _rss.parse(_rss.fetch(feed_url))
            if not feed.entries and not feed.title:
                continue
            out.append(RawCandidate(
                medium="blog",
                handle=_handle_from(feed, feed_url),
                source_url=feed_url,
                name=feed.title or None,
                bio=_rss.summarize_titles(feed.entries, "ブログ記事"),
                genre="blog",
                url=feed.link or feed_url,
            ))
        except Exception as exc:  # noqa: BLE001 — 1フィード失敗で他を止めない
            print(f"[blog_rss] {feed_url} 取得失敗: {exc}")
    return out
