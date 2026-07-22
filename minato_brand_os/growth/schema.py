from __future__ import annotations

"""Growth Engine 共通スキーマ。

RawCandidate = SourceAdapterが返す「発見の生データ」。
媒体を問わない（X/note/ブログ/YouTube…）。Normalizerがこれを候補DBの形へ正規化する。
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RawCandidate:
    medium: str                 # x / note / blog / youtube ...
    handle: str                 # 媒体内の一意なID（note=ユーザー名, x=スクリーンネーム）
    source_url: str             # 発見の根拠URL（discoveries台帳に保存）
    name: str | None = None
    bio: str | None = None
    genre: str | None = None
    url: str | None = None      # 本人ページURL（無ければ媒体から組み立て）
    followers: int | None = None
    # クロス媒体の統合証拠。本人が自分のプロフィール等に明記したリンクのみ入れる。
    # 例: {"x": "https://x.com/xxxx"}  ※表示名の類似などを根拠に入れてはならない
    self_links: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
