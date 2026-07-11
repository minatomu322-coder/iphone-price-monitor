from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = BASE_DIR / "config.mbos.yaml"
DEFAULT_DB = BASE_DIR / "data" / "brand_os.sqlite3"
SEEDS_DIR = BASE_DIR / "data" / "seeds"


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    p = Path(path) if path else DEFAULT_CONFIG
    with p.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    # star_thresholds / cadence_days のキーはYAMLで文字列化されうるのでint化
    cad = cfg.get("relationship", {}).get("cadence_days", {})
    cfg["relationship"]["cadence_days"] = {int(k): int(v) for k, v in cad.items()}
    return cfg
