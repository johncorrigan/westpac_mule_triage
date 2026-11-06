import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mule_triage.audit import AuditEvent, AuditLog, EventType, payload_json
from mule_triage.contracts import AccountId
from mule_triage.store import open_store

AS_OF = datetime(2026, 11, 3, tzinfo=UTC)
AT = datetime(2026, 11, 8, 3, 30, tzinfo=UTC)


def event(note: str) -> AuditEvent:
    return AuditEvent(
        EventType.DISPOSED,
        AccountId("ACC-101"),
        AS_OF,
        AT,
        payload_json({"note": note}),
    )


@pytest.fixture
def audited(tmp_path: Path) -> Path:
    database = tmp_path / "triage.db"
    with open_store(database) as store:
        log = AuditLog(store.connection)
        with store.transaction():
            log.create()
            log.append(event("first"))
    return database


def rows(database: Path) -> list[tuple[object, ...]]:
    with closing(sqlite3.connect(database)) as connection:
        return connection.execute("SELECT * FROM audit_events").fetchall()


def test_appends_events_and_reads_a_cases_history_in_order(audited: Path) -> None:
    with open_store(audited) as store:
        log = AuditLog(store.connection)
        with store.transaction():
            log.append(event("second"))

        history = log.history(AccountId("ACC-101"), AS_OF)

    assert [e.payload for e in history] == ['{"note":"first"}', '{"note":"second"}']
    assert history[0].occurred_at == AT


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET payload = '{}'",
        "DELETE FROM audit_events",
        "INSERT OR REPLACE INTO audit_events"
        " (event_id, event_type, account_id, as_of, occurred_at, payload)"
        " SELECT event_id, event_type, account_id, as_of, occurred_at, '{}'"
        " FROM audit_events",
    ],
    ids=["update", "delete", "insert or replace"],
)
def test_the_audit_table_refuses_to_change_or_remove_an_event(
    audited: Path, statement: str
) -> None:
    before = rows(audited)

    with closing(sqlite3.connect(audited)) as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(statement)

    assert rows(audited) == before
