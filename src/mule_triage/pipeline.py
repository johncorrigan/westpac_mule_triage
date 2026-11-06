import sqlite3
from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from mule_triage.audit import AuditEvent, AuditLog, EventType, payload_json
from mule_triage.clock import Clock
from mule_triage.contracts import (
    AccountProfile,
    AccountResult,
    Alert,
    IngestCounts,
    Payment,
    RulesConfig,
    RunResult,
    TransactionId,
)
from mule_triage.errors import ReconciliationError
from mule_triage.evidence import EvidencePack, build_pack, facts_sha256
from mule_triage.ingest import ParsedFile, parse_payments, parse_profiles, read_lines
from mule_triage.report import write_reports
from mule_triage.signals import activities
from mule_triage.store import (
    CaseChange,
    CaseSnapshot,
    CaseVersion,
    ChangeKind,
    FileTally,
    InputFile,
    RunId,
    RunTally,
    open_store,
)


@dataclass(frozen=True, slots=True)
class Settings:
    database: Path
    rules: RulesConfig
    out: Path
    clock: Clock


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
        snapshots: tuple[CaseSnapshot, ...],
    ) -> tuple[CaseChange, ...]: ...

    def case_versions(self, as_of: datetime) -> tuple[CaseVersion, ...]: ...

    @property
    def connection(self) -> sqlite3.Connection: ...


def run(
    payments: Path,
    profiles: Path,
    as_of: datetime,
    settings: Settings,
    store: RunStore | None = None,
) -> RunResult:
    if store is None:
        with open_store(settings.database) as opened:
            return triage(payments, profiles, as_of, settings, opened)
    return triage(payments, profiles, as_of, settings, store)


def triage(
    payments: Path,
    profiles: Path,
    as_of: datetime,
    settings: Settings,
    store: RunStore,
) -> RunResult:
    """Signals read every stored payment, because on a re-run every line is a duplicate.
    Each case change is audited in the run's transaction; reports are written after the
    commit, for every case at this as-of time."""
    parsed_payments = parse_payments(read_lines(payments))
    parsed_profiles = parse_profiles(read_lines(profiles))
    log = AuditLog(store.connection)
    with store.transaction():
        log.create()
        tally = ingest(as_of, parsed_payments, parsed_profiles, store)
        packs = evidence_packs(
            store.stored_payments(), store.stored_profiles(), as_of, settings.rules
        )
        changes = store.save_outcomes(
            tally.run_id,
            settings.rules,
            tuple(CaseSnapshot(pack.result, facts_sha256(pack)) for pack in packs),
        )
        at = settings.clock.now()
        for change in changes:
            log.append(case_event(change, as_of, at, tally.run_id))
        versions = store.case_versions(as_of)
    by_account = {pack.account_id: pack for pack in packs}
    write_reports(
        settings.out, ((by_account[case.account_id], case.version) for case in versions)
    )
    return run_result(tally.payments, tuple(pack.result for pack in packs))


CASE_EVENTS = {
    ChangeKind.OPENED: EventType.CASE_OPENED,
    ChangeKind.CHANGED: EventType.CASE_CHANGED,
    ChangeKind.REOPENED: EventType.CASE_REOPENED,
}


def case_event(
    change: CaseChange, as_of: datetime, at: datetime, run_id: RunId
) -> AuditEvent:
    result = change.snapshot.result
    return AuditEvent(
        CASE_EVENTS[change.kind],
        result.account_id,
        as_of,
        at,
        payload_json(
            {
                "run_id": run_id,
                "version": change.version,
                "outcome": result.outcome.value,
                "priority": (
                    result.priority.value if isinstance(result, Alert) else None
                ),
                "incomplete": isinstance(result, Alert) and result.incomplete,
                "reasons": sorted(result.reasons),
                "facts_sha256": change.snapshot.facts_sha256,
            }
        ),
    )


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


def evidence_packs(
    payments: Iterable[Payment],
    profiles: Iterable[AccountProfile],
    as_of: datetime,
    rules: RulesConfig,
) -> tuple[EvidencePack, ...]:
    """One per account, and each account's outcome comes from its pack."""
    return tuple(
        build_pack(activity, rules)
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
