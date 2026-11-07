import logging
import sqlite3
from collections import Counter
from collections.abc import Iterable
from contextlib import AbstractContextManager
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Protocol

from mule_triage.decision.audit import AuditEvent, AuditLog, EventType, payload_json
from mule_triage.clock import Clock
from mule_triage.contracts import (
    AccountProfile,
    AccountResult,
    Alert,
    CaseCounts,
    IngestCounts,
    NarrativeOutcome,
    NarrativeSummary,
    NoAlert,
    Outcome,
    Payment,
    RulesConfig,
    RunId,
    RunResult,
    TransactionId,
)
from mule_triage.errors import ReconciliationError
from mule_triage.evidence import EvidencePack, build_pack, facts_sha256
from mule_triage.logs import log_event
from mule_triage.records.ingest import (
    ParsedFile,
    parse_payments,
    parse_profiles,
    read_lines,
)
from mule_triage.narrative.client import ModelClient, OffClient
from mule_triage.narrative.narrator import NarrativeRecord, narrate
from mule_triage.narrative.template import TemplateNarrative
from mule_triage.narrative.validator import Accepted
from mule_triage.report import write_reports
from mule_triage.detection.signals import activities
from mule_triage.records.store import (
    CaseChange,
    CaseSnapshot,
    CaseVersion,
    ChangeKind,
    FileTally,
    InputFile,
    RunTally,
    open_store,
)


@dataclass(frozen=True, slots=True)
class Settings:
    """`cli.py` passes the client for `--ai`; without one, nothing is drafted."""

    database: Path
    rules: RulesConfig
    out: Path
    clock: Clock
    client: ModelClient = OffClient()


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
    Each case change is audited in the run's transaction. Each opened or changed ALERT and
    NEEDS_REVIEW case is narrated after the commit, so a slow model never holds the
    database, and its narrative is audited in a second transaction. Reports are written
    last, for every case at this as-of time."""
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
    result = run_result(
        tally, tuple(pack.result for pack in packs), case_counts(changes, versions)
    )
    log_stored(result)
    by_account = {pack.account_id: pack for pack in packs}
    narrated = tuple(
        (
            narrate(by_account[change.snapshot.result.account_id], settings.client),
            change,
        )
        for change in changes
        if not isinstance(change.snapshot.result, NoAlert)
    )
    with store.transaction():
        at = settings.clock.now()
        for record, change in narrated:
            log.append(narrative_event(record, change, as_of, at, tally.run_id))
    log_narratives(tally.run_id, tuple(record for record, _ in narrated))
    write_reports(
        settings.out, ((by_account[case.account_id], case.version) for case in versions)
    )
    log_event(
        __name__, logging.INFO, "reports_written", tally.run_id, cases=len(versions)
    )
    return replace(
        result,
        narratives=tuple(
            NarrativeSummary(record.pack.account_id, record.outcome)
            for record, _ in narrated
        ),
    )


def case_counts(
    changes: tuple[CaseChange, ...], versions: tuple[CaseVersion, ...]
) -> CaseCounts:
    """Every change is to a case at the run's as-of time, so the rest are unchanged."""
    created = sum(change.kind is ChangeKind.OPENED for change in changes)
    return CaseCounts(created, len(changes) - created, len(versions) - len(changes))


def log_stored(result: RunResult) -> None:
    outcomes = Counter(account.outcome for account in result.accounts)
    log_event(
        __name__,
        logging.INFO,
        "run_stored",
        result.run_id,
        payments=asdict(result.payment_counts),
        profiles=asdict(result.profile_counts),
        outcomes={outcome.value: outcomes[outcome] for outcome in Outcome},
        cases=asdict(result.cases),
    )


def log_narratives(run_id: RunId, records: tuple[NarrativeRecord, ...]) -> None:
    """A live call's latency is logged here, never in the run summary."""
    for record in records:
        if record.latency_seconds is not None:
            log_event(
                __name__,
                logging.INFO,
                "model_call",
                run_id,
                outcome=record.outcome.value,
                seconds=round(record.latency_seconds, 3),
            )
    outcomes = Counter(record.outcome for record in records)
    log_event(
        __name__,
        logging.INFO,
        "narratives_stored",
        run_id,
        outcomes={outcome.value: outcomes[outcome] for outcome in NarrativeOutcome},
    )


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


def narrative_event(
    record: NarrativeRecord,
    change: CaseChange,
    as_of: datetime,
    at: datetime,
    run_id: RunId,
) -> AuditEvent:
    label, narrative, verdict = record.label, record.narrative, record.verdict
    return AuditEvent(
        EventType.NARRATIVE,
        record.pack.account_id,
        as_of,
        at,
        payload_json(
            {
                "run_id": run_id,
                "version": change.version,
                "outcome": record.outcome.value,
                "source": None if label is None else label.source.value,
                "model": None if label is None else label.model,
                "digest": None if label is None else label.digest,
                "prompt_version": record.prompt_version,
                "facts_sha256": record.facts_sha256,
                "reply": record.reply,
                "verdict": (
                    None
                    if verdict is None
                    else "ACCEPTED" if isinstance(verdict, Accepted) else "REJECTED"
                ),
                "reasons": (
                    list(narrative.reasons)
                    if isinstance(narrative, TemplateNarrative)
                    else []
                ),
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


def ingest_counts(tally: FileTally) -> IngestCounts:
    return IngestCounts(
        received=tally.received,
        accepted=tally.accepted,
        rejected=len(tally.rejections),
        duplicates=len(tally.duplicate_ids),
    )


def run_result(
    tally: RunTally, accounts: tuple[AccountResult, ...], cases: CaseCounts
) -> RunResult:
    """Without narratives: they're drafted after the run's transaction commits."""
    payments = tally.payments
    return RunResult(
        run_id=tally.run_id,
        payment_counts=ingest_counts(payments),
        profile_counts=ingest_counts(tally.profiles),
        rejected_payment_ids=tuple(
            rejection.record_id
            for rejection in payments.rejections
            if rejection.record_id is not None
        ),
        duplicate_payment_ids=tuple(
            TransactionId(record_id) for record_id in payments.duplicate_ids
        ),
        accounts=accounts,
        cases=cases,
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
