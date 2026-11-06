from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from mule_triage.contracts import RunResult


@dataclass(frozen=True, slots=True)
class Settings:
    database: Path


def run(
    payments: Path, profiles: Path, as_of: datetime, settings: Settings
) -> RunResult:
    raise NotImplementedError("the pipeline is built in M3 and M4")
