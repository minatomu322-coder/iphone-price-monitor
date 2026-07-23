from __future__ import annotations

"""SourceAdapter: 手動シードCSV（data/seeds/*.csv）。

iPhoneのGitHub webエディタから編集できる、最も確実で高品質な発見経路。
"""

from typing import Any

from ...x_client import collect_seeds
from ..schema import RawCandidate


def discover(cfg: dict[str, Any]) -> list[RawCandidate]:
    out: list[RawCandidate] = []
    for acc in collect_seeds():
        out.append(RawCandidate(
            medium="x",
            handle=acc["handle"],
            source_url="data/seeds/*.csv",
            name=acc.get("name"),
            bio=acc.get("bio"),
            genre=acc.get("genre"),
            url=f"https://x.com/{acc['handle']}",
            followers=acc.get("followers"),
            raw=acc,
        ))
    return out
