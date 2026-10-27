from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .config import APP_DIR
from .session_stats import diff_snapshots

LIFETIME_FILE = APP_DIR / "lifetime_stats.json"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ModeTotals:
    battles: int = 0
    wins: int = 0
    losses: int = 0
    unknown: int = 0

    @property
    def win_rate(self) -> float:
        d = self.wins + self.losses
        return (self.wins / d * 100.0) if d else 0.0


class LifetimeTracker:
    def __init__(self):
        self.created_at = _utcnow()
        self.updated_at = _utcnow()
        self.baseline_snapshot: dict[str, Any] | None = None
        self.current_snapshot: dict[str, Any] | None = None
        self.modes: dict[str, ModeTotals] = {}
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(LIFETIME_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.created_at = str(raw.get("created_at") or self.created_at)
                self.updated_at = str(raw.get("updated_at") or self.updated_at)
                self.baseline_snapshot = raw.get("baseline_snapshot") if isinstance(raw.get("baseline_snapshot"), dict) else None
                self.current_snapshot = raw.get("current_snapshot") if isinstance(raw.get("current_snapshot"), dict) else None
                modes = raw.get("modes") or {}
                if isinstance(modes, dict):
                    for name, row in modes.items():
                        if isinstance(row, dict):
                            self.modes[str(name)] = ModeTotals(
                                battles=int(row.get("battles") or 0),
                                wins=int(row.get("wins") or 0),
                                losses=int(row.get("losses") or 0),
                                unknown=int(row.get("unknown") or 0),
                            )
        except Exception:
            pass

    def ensure_baseline(self, snapshot: dict[str, Any]) -> None:
        if self.baseline_snapshot is None:
            self.baseline_snapshot = snapshot
        self.current_snapshot = snapshot
        self.updated_at = _utcnow()
        self.save()

    def update_snapshot(self, snapshot: dict[str, Any]) -> None:
        if self.baseline_snapshot is None:
            self.baseline_snapshot = snapshot
        self.current_snapshot = snapshot
        self.updated_at = _utcnow()
        self.save()

    def record_battle(self, mode: str, won: bool | None) -> None:
        key = str(mode or "Unknown")
        t = self.modes.setdefault(key, ModeTotals())
        t.battles += 1
        if won is True:
            t.wins += 1
        elif won is False:
            t.losses += 1
        else:
            t.unknown += 1
        self.updated_at = _utcnow()
        self.save()

    def gains(self) -> list[dict[str, Any]]:
        return diff_snapshots(self.baseline_snapshot, self.current_snapshot)

    def to_dict(self) -> dict[str, Any]:
        return {
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "baseline_snapshot": self.baseline_snapshot,
            "current_snapshot": self.current_snapshot,
            "modes": {k: asdict(v) for k, v in self.modes.items()},
        }

    def save(self) -> None:
        try:
            APP_DIR.mkdir(parents=True, exist_ok=True)
            LIFETIME_FILE.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        except Exception:
            pass
