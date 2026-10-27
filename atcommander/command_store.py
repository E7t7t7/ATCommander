"""Atomic local state for command preferences, deck backups and subscriptions."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path


class CommandStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def read(self) -> dict:
        with self.lock:
            try:
                result = json.loads(self.path.read_text(encoding="utf-8"))
                return result if isinstance(result, dict) else {}
            except (OSError, ValueError):
                return {}

    def update(self, key: str, value):
        with self.lock:
            data = self.read()
            data[key] = value
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, self.path)
            return data
