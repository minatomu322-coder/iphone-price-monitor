from __future__ import annotations

"""SourceAdapter: note 公式RSS。

CEO承認条件5:
    - 公式RSS内の情報だけを使う（プロフィールページ等の外部取得はP0ではしない）
    - 取得元URL・取得日時はdiscoveries台帳に保存（Deduplicator側で記録）
    - Xリンクが無い人物も note上の見込み顧客(medium='note') として保存する

noteはユーザー/マガジン単位の公式RSSを提供している:
    https://note.com/<username>/rss
config(growth.sources.note_rss.feeds)にフィードURLを列挙して使う。
"""

import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.request import Request, urlopen

from ..schema import RawCandidate

USER_RE = re.compile(r"note\.com/([A-Za-z0-9_]+)")
TIMEOUT = 15
UA = "MinatoBrandOS/0.1 (+official RSS reader)"


def _fetch(url: str) -> str:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 — httpsの公式RSSのみ
        return resp.read().decode("utf-8", errors="replace")


def _parse_feed(xml_text: str, feed_url: str) -> list[RawCandidate]:
    root = ET.fromstring(xml_text)
    channel = root.find("channel")
    if channel is None:
        return []
    out: dict[str, RawCandidate] = {}
    creator_name = (channel.findtext("title") or "").replace("の新着記事", "").strip()
    for item in channel.findall("item"):
        link = item.findtext("link") or ""
        m = USER_RE.search(link)
        if not m:
            continue
        username = m.group(1)
        title = (item.findtext("title") or "").strip()
        cand = out.get(username)
        if cand is None:
            out[username] = RawCandidate(
                medium="note",
                handle=username,
                source_url=feed_url,
                name=creator_name or username,
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
            out.extend(_parse_feed(_fetch(feed_url), feed_url))
        except Exception as exc:  # noqa: BLE001 — 1フィード失敗で他を止めない
            print(f"[note_rss] {feed_url} 取得失敗: {exc}")
    return out
