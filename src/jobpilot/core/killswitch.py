"""Global kill switch. A flag file so every process (CLI, web, scheduler) sees it."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path


class KillSwitchEngaged(RuntimeError):
    pass


class KillSwitch:
    def __init__(self, data_dir: Path) -> None:
        self._flag = data_dir / "KILL_SWITCH"

    def engage(self, reason: str = "user requested stop") -> None:
        self._flag.parent.mkdir(parents=True, exist_ok=True)
        self._flag.write_text(f"{datetime.now(UTC).isoformat()} {reason}\n")

    def release(self) -> None:
        self._flag.unlink(missing_ok=True)

    @property
    def engaged(self) -> bool:
        return self._flag.exists()

    def reason(self) -> str:
        return self._flag.read_text().strip() if self.engaged else ""

    def check(self) -> None:
        """Call before every agent step and every browser or mail side effect."""
        if self.engaged:
            raise KillSwitchEngaged(self.reason())
