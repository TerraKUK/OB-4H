"""Persistent state for active Order Blocks and divergence alerts."""

from __future__ import annotations

import json
from pathlib import Path


STATE_PATH = Path("data/state/active_obs.json")


def load_state(path: Path = STATE_PATH) -> dict:
    if not path.exists():
        return {"version": 2, "zones": {}, "divergences": {}}
    with path.open(encoding="utf-8") as file:
        state = json.load(file)
    state.setdefault("version", 2)
    state.setdefault("zones", {})
    state.setdefault("divergences", {})
    return state


def save_state(state: dict, path: Path = STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(state, file, ensure_ascii=False, indent=2, sort_keys=True)
        file.write("\n")
