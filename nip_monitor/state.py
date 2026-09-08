from __future__ import annotations

import json
from pathlib import Path


DEFAULT_STATE = {
    "version": 1,
    "initialized": False,
    "news_ids": [],
    "matches": {},
    "transfer_ids": [],
    "sent_reminders": [],
    "recent_result_ids": [],
    "updated_at": "",
}


def load_state(path: Path) -> dict:
    if not path.exists():
        return DEFAULT_STATE.copy()
    with path.open("r", encoding="utf-8") as handle:
        loaded = json.load(handle)
    state = DEFAULT_STATE.copy()
    state.update(loaded)
    return state


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)
