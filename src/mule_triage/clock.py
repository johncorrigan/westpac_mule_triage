"""Time, injected: `cli.py` passes the wall clock, and tests pass a fixed one."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


@dataclass(frozen=True, slots=True)
class FixedClock:
    moment: datetime

    def now(self) -> datetime:
        return self.moment
