from __future__ import annotations

"""RSS/Atom 共通パーサ（SourceAdapter用の内部ヘルパー）。

対応形式:
    - RSS 2.0 (note・はてな・WordPress等のブログ)
    - Atom    (YouTubeチャンネルの公式フィード等)
公式に公開されているフィードのみを対象とする（HTMLスクレイピングはしない）。
"""

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from urllib.request import Request, urlopen

TIMEOUT = 15
UA = "MinatoBrandOS/0.1 (+official RSS/Atom reader)"
ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass
class Feed:
    title: str = ""
    link: str = ""
    author_uri: str = ""          # Atomのみ（YouTubeはチャンネルURL）
    entries: list[dict] = field(default_factory=list)  # {title, link, author}


def fetch(url: str) -> str:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310 — 公式フィードのみ
        return resp.read().decode("utf-8", errors="replace")


def parse(xml_text: str) -> Feed:
    root = ET.fromstring(xml_text)
    feed = Feed()
    if root.tag == f"{ATOM}feed":  # Atom (YouTube等)
        feed.title = (root.findtext(f"{ATOM}title") or "").strip()
        author = root.find(f"{ATOM}author")
        if author is not None:
            feed.author_uri = (author.findtext(f"{ATOM}uri") or "").strip()
        link = root.find(f"{ATOM}link[@rel='alternate']")
        feed.link = link.get("href", "") if link is not None else feed.author_uri
        for e in root.findall(f"{ATOM}entry"):
            elink = e.find(f"{ATOM}link")
            feed.entries.append({
                "title": (e.findtext(f"{ATOM}title") or "").strip(),
                "link": elink.get("href", "") if elink is not None else "",
                "author": (e.findtext(f"{ATOM}author/{ATOM}name") or "").strip(),
            })
    else:  # RSS 2.0
        channel = root.find("channel")
        if channel is None:
            return feed
        feed.title = (channel.findtext("title") or "").strip()
        feed.link = (channel.findtext("link") or "").strip()
        for item in channel.findall("item"):
            feed.entries.append({
                "title": (item.findtext("title") or "").strip(),
                "link": (item.findtext("link") or "").strip(),
                "author": (item.findtext("{http://purl.org/dc/elements/1.1/}creator") or "").strip(),
            })
    return feed


def summarize_titles(entries: list[dict], prefix: str, limit: int = 200) -> str:
    text = " / ".join(e["title"] for e in entries[:5] if e["title"])
    return f"{prefix}: {text}"[:limit]
