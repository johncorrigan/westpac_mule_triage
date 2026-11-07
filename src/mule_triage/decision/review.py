"""A reviewer's disposition of one case, and the view of a case they decide on.

A disposition names the version the reviewer saw, and succeeds only while that's current: one
UPDATE ... WHERE version = ?, in the same BEGIN IMMEDIATE transaction as its audit event, so of
two reviewers who saw the same version exactly one wins. A refusal is a result, not an exception.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TypeAlias

from mule_triage.decision.audit import AuditEvent, AuditLog, EventType, payload_json
from mule_triage.clock import Clock
from mule_triage.contracts import AccountId, Alert, RulesConfig
from mule_triage.evidence import EvidencePack, build_pack, facts_sha256
from mule_triage.detection.signals import activities
from mule_triage.records.store import SqliteStore, StoredCase


class DispositionReason(StrEnum):
    ESCALATED = "ESCALATED"
    FALSE_POSITIVE = "FALSE_POSITIVE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    NO_FURTHER_ACTION = "NO_FURTHER_ACTION"


class NarrativeUse(StrEnum):
    ACCEPTED = "ACCEPTED"
    EDITED = "EDITED"
    DISCARDED = "DISCARDED"


@dataclass(frozen=True, slots=True)
class Disposition:
    account_id: AccountId
    as_of: datetime
    seen_version: int
    reason: DispositionReason
    reviewer: str
    narrative: NarrativeUse


@dataclass(frozen=True, slots=True)
class Disposed:
    account_id: AccountId
    as_of: datetime
    version: int


@dataclass(frozen=True, slots=True)
class Stale:
    account_id: AccountId
    as_of: datetime
    seen_version: int
    current_version: int

    @property
    def message(self) -> str:
        return (
            f"the case is at version {self.current_version}, not {self.seen_version}:"
            " re-read the case before disposing of it"
        )


@dataclass(frozen=True, slots=True)
class CaseNotFound:
    account_id: AccountId
    as_of: datetime


DispositionResult: TypeAlias = Disposed | Stale | CaseNotFound


def dispose(
    store: SqliteStore, disposition: Disposition, clock: Clock
) -> DispositionResult:
    log = AuditLog(store.connection)
    account, as_of = disposition.account_id, disposition.as_of
    with store.transaction():
        if store.close_case(account, as_of, disposition.seen_version):
            version = disposition.seen_version + 1
            closed = store.case(account, as_of)
            assert closed is not None, "a case just closed is still stored"
            log.append(
                AuditEvent(
                    EventType.DISPOSED,
                    account,
                    as_of,
                    clock.now(),
                    payload_json(
                        {
                            "reason": disposition.reason.value,
                            "reviewer": disposition.reviewer,
                            "narrative": disposition.narrative.value,
                            "seen_version": disposition.seen_version,
                            "version": version,
                            "facts_sha256": closed.facts_sha256,
                        }
                    ),
                )
            )
            return Disposed(account, as_of, version)
        current = store.case(account, as_of)
    if current is None:
        return CaseNotFound(account, as_of)
    return Stale(account, as_of, disposition.seen_version, current.version)


@dataclass(frozen=True, slots=True)
class CaseView:
    case: StoredCase
    pack: EvidencePack
    history: tuple[AuditEvent, ...]

    @property
    def matches_case(self) -> bool:
        """False when the pack, rebuilt with the given rules, shows another outcome or other
        facts than the stored case, as an outcome setting changes no fact."""
        result, case = self.pack.result, self.case
        priority = result.priority.value if isinstance(result, Alert) else None
        incomplete = isinstance(result, Alert) and result.incomplete
        rebuilt = (
            result.outcome.value,
            priority,
            incomplete,
            tuple(sorted(result.reasons)),
        )
        stored = (case.outcome, case.priority, case.incomplete, case.reasons)
        return rebuilt == stored and facts_sha256(self.pack) == case.facts_sha256


def case_view(
    store: SqliteStore, account_id: AccountId, as_of: datetime, rules: RulesConfig
) -> CaseView | None:
    case = store.case(account_id, as_of)
    if case is None:
        return None
    found = activities(store.stored_payments(), store.stored_profiles(), as_of, rules)
    (activity,) = [each for each in found if each.account_id == account_id]
    history = AuditLog(store.connection).history(account_id, as_of)
    return CaseView(case, build_pack(activity, rules), history)
