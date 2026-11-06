from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from mule_triage.contracts import AccountProfile, Payment, RunResult
from mule_triage.errors import ReconciliationError
from mule_triage.ingest import ParsedFile, parse_payments, parse_profiles, read_lines
from mule_triage.store import FileTally, InputFile, RunId, RunTally, open_store


@dataclass(frozen=True, slots=True)
class Settings:
    database: Path


class RunStore(Protocol):
    """`tally` takes only a run ID, so it can only count what was stored."""

    def transaction(self) -> AbstractContextManager[None]: ...

    def save(
        self,
        as_of: datetime,
        payments: ParsedFile[Payment],
        profiles: ParsedFile[AccountProfile],
    ) -> RunId: ...

    def tally(self, run_id: RunId) -> RunTally: ...


def run(
    payments: Path,
    profiles: Path,
    as_of: datetime,
    settings: Settings,
    store: RunStore | None = None,
) -> RunResult:
    if store is None:
        with open_store(settings.database) as opened:
            return triage(payments, profiles, as_of, opened)
    return triage(payments, profiles, as_of, store)


def triage(
    payments: Path, profiles: Path, as_of: datetime, store: RunStore
) -> RunResult:
    record_run(payments, profiles, as_of, store)
    raise NotImplementedError("signals, rules and outcomes are built in M4")


def record_run(
    payments: Path, profiles: Path, as_of: datetime, store: RunStore
) -> RunTally:
    parsed_payments = parse_payments(read_lines(payments))
    parsed_profiles = parse_profiles(read_lines(profiles))
    with store.transaction():
        run_id = store.save(as_of, parsed_payments, parsed_profiles)
        tally = store.tally(run_id)
        reconcile(tally)
    return tally


def reconcile(tally: RunTally) -> None:
    problems = [
        imbalance(file, file_tally)
        for file, file_tally in (
            (InputFile.PAYMENTS, tally.payments),
            (InputFile.PROFILES, tally.profiles),
        )
        if recorded(file_tally) != file_tally.received
    ]
    if problems:
        raise ReconciliationError("; ".join(problems))


def recorded(tally: FileTally) -> int:
    return tally.accepted + len(tally.rejections) + len(tally.duplicate_ids)


def imbalance(file: InputFile, tally: FileTally) -> str:
    return (
        f"{file}: received {tally.received} lines, but stored {tally.accepted}"
        f" accepted + {len(tally.rejections)} rejected"
        f" + {len(tally.duplicate_ids)} duplicates = {recorded(tally)}"
    )
