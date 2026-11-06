from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from mule_triage.contracts import (
    AccountProfile,
    AccountResult,
    IngestCounts,
    Payment,
    RulesConfig,
    RunResult,
    TransactionId,
)
from mule_triage.errors import ReconciliationError
from mule_triage.ingest import ParsedFile, parse_payments, parse_profiles, read_lines
from mule_triage.rules import outcome_for
from mule_triage.signals import activities, assess
from mule_triage.store import FileTally, InputFile, RunId, RunTally, open_store


@dataclass(frozen=True, slots=True)
class Settings:
    database: Path
    rules: RulesConfig


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

    def stored_payments(self) -> tuple[Payment, ...]: ...

    def stored_profiles(self) -> tuple[AccountProfile, ...]: ...

    def save_outcomes(
        self,
        run_id: RunId,
        rules: RulesConfig,
        results: tuple[AccountResult, ...],
    ) -> None: ...


def run(
    payments: Path,
    profiles: Path,
    as_of: datetime,
    settings: Settings,
    store: RunStore | None = None,
) -> RunResult:
    if store is None:
        with open_store(settings.database) as opened:
            return triage(payments, profiles, as_of, settings.rules, opened)
    return triage(payments, profiles, as_of, settings.rules, store)


def triage(
    payments: Path,
    profiles: Path,
    as_of: datetime,
    rules: RulesConfig,
    store: RunStore,
) -> RunResult:
    """Signals read every stored payment, because on a re-run every line is a duplicate."""
    parsed_payments = parse_payments(read_lines(payments))
    parsed_profiles = parse_profiles(read_lines(profiles))
    with store.transaction():
        tally = ingest(as_of, parsed_payments, parsed_profiles, store)
        accounts = outcomes(
            store.stored_payments(), store.stored_profiles(), as_of, rules
        )
        store.save_outcomes(tally.run_id, rules, accounts)
    return run_result(tally.payments, accounts)


def record_run(
    payments: Path, profiles: Path, as_of: datetime, store: RunStore
) -> RunTally:
    parsed_payments = parse_payments(read_lines(payments))
    parsed_profiles = parse_profiles(read_lines(profiles))
    with store.transaction():
        return ingest(as_of, parsed_payments, parsed_profiles, store)


def ingest(
    as_of: datetime,
    payments: ParsedFile[Payment],
    profiles: ParsedFile[AccountProfile],
    store: RunStore,
) -> RunTally:
    run_id = store.save(as_of, payments, profiles)
    tally = store.tally(run_id)
    reconcile(tally)
    return tally


def outcomes(
    payments: Iterable[Payment],
    profiles: Iterable[AccountProfile],
    as_of: datetime,
    rules: RulesConfig,
) -> tuple[AccountResult, ...]:
    return tuple(
        outcome_for(activity.account_id, assess(activity, rules), rules)
        for activity in activities(payments, profiles, as_of, rules)
    )


def run_result(payments: FileTally, accounts: tuple[AccountResult, ...]) -> RunResult:
    return RunResult(
        payment_counts=IngestCounts(
            received=payments.received,
            accepted=payments.accepted,
            rejected=len(payments.rejections),
            duplicates=len(payments.duplicate_ids),
        ),
        rejected_payment_ids=tuple(
            rejection.record_id
            for rejection in payments.rejections
            if rejection.record_id is not None
        ),
        duplicate_payment_ids=tuple(
            TransactionId(record_id) for record_id in payments.duplicate_ids
        ),
        accounts=accounts,
    )


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
