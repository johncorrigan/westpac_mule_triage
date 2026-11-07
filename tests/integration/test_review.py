import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from mule_triage.decision.audit import AuditEvent, AuditLog, EventType, payload_json
from mule_triage.clock import FixedClock
from mule_triage.contracts import AccountId, RulesConfig
from mule_triage.pipeline import Settings, run
from mule_triage.decision.review import (
    CaseNotFound,
    Disposed,
    Disposition,
    DispositionReason,
    NarrativeUse,
    Stale,
    case_view,
    dispose,
)
from mule_triage.records.store import CaseStatus, open_store

AS_OF = datetime(2026, 11, 3, tzinfo=UTC)
RUN_AT = datetime(2026, 11, 8, 3, 0, tzinfo=UTC)
REVIEWED_AT = datetime(2026, 11, 8, 3, 30, tzinfo=UTC)
GOLDEN = Path(__file__).resolve().parents[1] / "golden"
ACC_101 = AccountId("ACC-101")
EXTRA_PAYMENT = {
    "transaction_id": "TX-101-99",
    "from_account": "ACC-208",
    "to_account": "ACC-101",
    "amount_cents": 90000,
    "event_time": "2026-11-02T11:45:00Z",
    "knowledge_time": "2026-11-02T11:46:00Z",
    "reference": "rent",
}


def run_golden(
    database: Path, rules: RulesConfig, payments: Path = GOLDEN / "payments.jsonl"
) -> None:
    settings = Settings(database, rules, database.parent / "out", FixedClock(RUN_AT))
    run(payments, GOLDEN / "profiles.jsonl", AS_OF, settings)


def with_extra_payment(folder: Path) -> Path:
    payments = folder / "payments.jsonl"
    golden = (GOLDEN / "payments.jsonl").read_text(encoding="utf-8")
    payments.write_text(golden + json.dumps(EXTRA_PAYMENT) + "\n", encoding="utf-8")
    return payments


def disposition(seen_version: int, reviewer: str = "REV-1") -> Disposition:
    return Disposition(
        account_id=ACC_101,
        as_of=AS_OF,
        seen_version=seen_version,
        reason=DispositionReason.ESCALATED,
        reviewer=reviewer,
        narrative=NarrativeUse.EDITED,
    )


def dispose_in(database: Path, chosen: Disposition) -> Disposed | Stale | CaseNotFound:
    with open_store(database) as store:
        return dispose(store, chosen, FixedClock(REVIEWED_AT))


def history(database: Path) -> list[tuple[EventType, dict[str, object]]]:
    with open_store(database) as store:
        events = AuditLog(store.connection).history(ACC_101, AS_OF)
    return [(e.event_type, json.loads(e.payload)) for e in events]


def test_each_new_case_is_an_audit_event_with_its_run(
    tmp_path: Path, rules: RulesConfig
) -> None:
    database = tmp_path / "triage.db"

    run_golden(database, rules)

    (event_type, payload), (narrated, _) = history(database)
    assert event_type is EventType.CASE_OPENED
    assert (payload["run_id"], payload["version"]) == (1, 1)
    assert narrated is EventType.NARRATIVE


def test_a_rerun_of_the_same_files_adds_no_audit_event(
    tmp_path: Path, rules: RulesConfig
) -> None:
    database = tmp_path / "triage.db"
    run_golden(database, rules)

    run_golden(database, rules)

    assert [event_type for event_type, _ in history(database)] == [
        EventType.CASE_OPENED,
        EventType.NARRATIVE,
    ]


def test_a_change_to_an_open_case_is_audited_as_changed_not_reopened(
    tmp_path: Path, rules: RulesConfig
) -> None:
    database = tmp_path / "triage.db"
    run_golden(database, rules)

    run_golden(database, rules, payments=with_extra_payment(tmp_path))

    events = history(database)
    assert [event_type for event_type, _ in events] == [
        EventType.CASE_OPENED,
        EventType.NARRATIVE,
        EventType.CASE_CHANGED,
        EventType.NARRATIVE,
    ]
    assert (events[2][1]["run_id"], events[2][1]["version"]) == (2, 2)


def test_a_disposition_on_the_current_version_closes_the_case_and_is_audited(
    tmp_path: Path, rules: RulesConfig
) -> None:
    database = tmp_path / "triage.db"
    run_golden(database, rules)

    result = dispose_in(database, disposition(seen_version=1))

    assert result == Disposed(ACC_101, AS_OF, version=2)
    with open_store(database) as store:
        view = case_view(store, ACC_101, AS_OF, rules)
    assert view is not None
    assert (view.case.status, view.case.version) == (CaseStatus.CLOSED, 2)
    *_, (event_type, payload) = history(database)
    assert event_type is EventType.DISPOSED
    assert payload == {
        "reason": "ESCALATED",
        "reviewer": "REV-1",
        "narrative": "EDITED",
        "seen_version": 1,
        "version": 2,
        "facts_sha256": view.case.facts_sha256,
    }
    assert view.history[-1].occurred_at == REVIEWED_AT


def test_of_two_reviewers_who_saw_the_same_version_only_the_first_wins(
    tmp_path: Path, rules: RulesConfig
) -> None:
    database = tmp_path / "triage.db"
    run_golden(database, rules)
    dispose_in(database, disposition(seen_version=1, reviewer="REV-1"))

    second = dispose_in(database, disposition(seen_version=1, reviewer="REV-2"))

    assert second == Stale(ACC_101, AS_OF, seen_version=1, current_version=2)
    assert "re-read the case" in second.message
    assert [t for t, _ in history(database)].count(EventType.DISPOSED) == 1


def test_a_disposition_for_a_case_that_doesnt_exist_is_not_found(
    tmp_path: Path, rules: RulesConfig
) -> None:
    database = tmp_path / "triage.db"
    run_golden(database, rules)
    unknown = Disposition(
        account_id=AccountId("ACC-999"),
        as_of=AS_OF,
        seen_version=1,
        reason=DispositionReason.FALSE_POSITIVE,
        reviewer="REV-1",
        narrative=NarrativeUse.DISCARDED,
    )

    result = dispose_in(database, unknown)

    assert result == CaseNotFound(AccountId("ACC-999"), AS_OF)


def test_one_more_payment_reopens_a_disposed_case_and_stales_its_old_version(
    tmp_path: Path, rules: RulesConfig
) -> None:
    database = tmp_path / "triage.db"
    run_golden(database, rules)
    dispose_in(database, disposition(seen_version=1))
    run_golden(database, rules, payments=with_extra_payment(tmp_path))

    late = dispose_in(database, disposition(seen_version=2))

    assert late == Stale(ACC_101, AS_OF, seen_version=2, current_version=3)
    with open_store(database) as store:
        view = case_view(store, ACC_101, AS_OF, rules)
    assert view is not None
    assert (view.case.status, view.case.version) == (CaseStatus.OPEN, 3)
    assert [t for t, _ in history(database)] == [
        EventType.CASE_OPENED,
        EventType.NARRATIVE,
        EventType.DISPOSED,
        EventType.CASE_REOPENED,
        EventType.NARRATIVE,
    ]
    assert history(database)[3][1]["run_id"] == 2


def test_a_case_view_shows_the_stored_case_and_its_rebuilt_pack(
    tmp_path: Path, rules: RulesConfig
) -> None:
    database = tmp_path / "triage.db"
    run_golden(database, rules)

    with open_store(database) as store:
        view = case_view(store, ACC_101, AS_OF, rules)

    assert view is not None
    assert view.case.version == 1
    assert view.matches_case
    assert view.pack.counterparties.note == "5 of 9 shown; 4 left out"


def test_a_narrative_event_written_without_its_sentences_gives_no_narrative(
    tmp_path: Path, rules: RulesConfig
) -> None:
    database = tmp_path / "triage.db"
    run_golden(database, rules)
    with open_store(database) as store:
        view = case_view(store, ACC_101, AS_OF, rules)
    assert view is not None
    older = AuditEvent(
        EventType.NARRATIVE,
        ACC_101,
        AS_OF,
        RUN_AT,
        payload_json(
            {
                "run_id": 1,
                "version": 1,
                "outcome": "NO_RECORDING",
                "source": None,
                "model": None,
                "digest": None,
                "prompt_version": "narrative-v1",
                "facts_sha256": view.case.facts_sha256,
                "reply": None,
                "verdict": None,
                "reasons": ["NO_RECORDING: no recording"],
            }
        ),
    )

    narrative = replace(view, history=(older,)).narrative

    assert narrative is None


def test_a_case_view_with_other_outcome_rules_doesnt_match_the_case(
    tmp_path: Path, rules: RulesConfig
) -> None:
    database = tmp_path / "triage.db"
    run_golden(database, rules)
    stricter = rules.model_copy(
        update={"outcome": rules.outcome.model_copy(update={"alert_at": 3})}
    )

    with open_store(database) as store:
        view = case_view(store, AccountId("ACC-106"), AS_OF, stricter)

    assert view is not None
    assert view.case.outcome == "ALERT"
    assert not view.matches_case
