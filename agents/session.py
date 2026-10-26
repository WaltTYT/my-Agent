
#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from typing import Any
import json

from .paths import folded_sessions_dir, sessions_dir


def save_session(session_id: str, data: dict[str, Any]) -> None:
    path = sessions_dir() / f"{session_id}.json"
    path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")


def save_folded_session_memory(session_id: str, record: dict[str, Any]) -> None:
    d = folded_sessions_dir()
    line = json.dumps(record, ensure_ascii=False, default=str)
    with (d / f"{session_id}.folded-memory.jsonl").open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    (d / f"{session_id}.folded-memory.latest.json").write_text(
        json.dumps(record, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def load_session(session_id: str) -> dict[str, Any] | None:
    path = sessions_dir() / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_sessions() -> list[dict[str, Any]]:
    results = []
    for f in sessions_dir().glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if "metadata" in data:
                results.append(data["metadata"])
        except Exception:
            pass
    return results


def get_latest_session_id() -> str | None:
    sessions = list_sessions()
    if not sessions:
        return None
    sessions.sort(key=lambda s: s.get("startTime", ""), reverse=True)
    return sessions[0].get("id")
