from __future__ import annotations

"""SourceAdapter: YouTubeチャンネルの公式RSSフィード。

YouTube Data APIではなく、YouTubeが公式に公開しているチャンネル別Atomフィード
    https://www.youtube.com/feeds/videos.xml?channel_id=UCxxxx
を読む。APIキー不要・無料・規約セーフ。
config(growth.sources.youtube_rss.feeds)にフィードURLを列挙して使う。
チャンネル運営者を medium='youtube' の見込み顧客として収集する。
"""

import re
from typing import Any

from ..schema import RawCandidate
from . import _rss

CHANNEL_ID_RE = re.compile(r"channel_id=([A-Za-z0-9_-]+)")


def discover(cfg: dict[str, Any]) -> list[RawCandidate]:
    feeds = (cfg.get("growth", {}).get("sources", {}).get("youtube_rss", {}) or {}).get("feeds", [])
    out: list[RawCandidate] = []
    for feed_url in feeds:
        try:
            feed = _rss.parse(_rss.fetch(feed_url))
            m = CHANNEL_ID_RE.search(feed_url)
            channel_id = m.group(1) if m else (feed.author_uri.rsplit("/", 1)[-1] or feed.title)
            if not channel_id:
                continue
            out.append(RawCandidate(
                medium="youtube",
                handle=channel_id,
                source_url=feed_url,
                name=feed.title or None,
                bio=_rss.summarize_titles(feed.entries, "動画"),
                genre="youtube",
                url=feed.author_uri or feed.link or feed_url,
            ))
        except Exception as exc:  # noqa: BLE001
            print(f"[youtube_rss] {feed_url} 取得失敗: {exc}")
    return out
