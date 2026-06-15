from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class ActiveSession:
    requested_by_user_id: int
    requested_by_username: str
    guild_id: int
    guild_name: str
    channel_id: int
    channel_name: str
    join_url: str
    started_at: str


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def get_active(self) -> ActiveSession | None:
        with self._lock:
            data = self._read()
            raw = data.get("active_session")
            if not raw:
                return None
            return ActiveSession(**raw)

    def set_active(self, session: ActiveSession) -> None:
        with self._lock:
            data = self._read()
            data["active_session"] = asdict(session)
            self._write(data)

    def clear_active(self) -> None:
        with self._lock:
            data = self._read()
            data["active_session"] = None
            self._write(data)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"active_session": None}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"active_session": None}

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp_path.replace(self.path)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
