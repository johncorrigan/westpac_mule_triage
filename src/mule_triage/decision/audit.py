"""The audit table: one append-only record of every audited event, for every case.

Triggers refuse UPDATE and DELETE, and an INSERT that reuses an event ID, because INSERT OR
REPLACE removes the old row without firing a delete trigger. The DDL runs statement by statement,
because `executescript` would commit any open transaction first.
"""

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from mule_triage.contracts import AccountId
from mule_triage.records.store import utc_text

DDL = (
    "CREATE TABLE IF NOT EXISTS audit_events ("
    " event_id INTEGER PRIMARY KEY AUTOINCREMENT,"
    " event_type TEXT NOT NULL,"
    " account_id TEXT NOT NULL,"
    " as_of TEXT NOT NULL,"
    " occurred_at TEXT NOT NULL,"
    " payload TEXT NOT NULL)",
    "CREATE TRIGGER IF NOT EXISTS audit_events_refuse_update"
    " BEFORE UPDATE ON audit_events"
    " BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS audit_events_refuse_delete"
    " BEFORE DELETE ON audit_events"
    " BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END",
    "CREATE TRIGGER IF NOT EXISTS audit_events_refuse_a_reused_id"
    " BEFORE INSERT ON audit_events"
    " WHEN EXISTS (SELECT 1 FROM audit_events WHERE event_id = NEW.event_id)"
    " BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END",
)
INSERT_EVENT = (
    "INSERT INTO audit_events (event_type, account_id, as_of, occurred_at, payload)"
    " VALUES (?, ?, ?, ?, ?)"
)
SELECT_HISTORY = (
    "SELECT event_type, account_id, occurred_at, payload FROM audit_events"
    " WHERE account_id = ? AND as_of = ? ORDER BY event_id"
)


class EventType(StrEnum):
    CASE_OPENED = "CASE_OPENED"
    CASE_CHANGED = "CASE_CHANGED"
    CASE_REOPENED = "CASE_REOPENED"
    DISPOSED = "DISPOSED"
    NARRATIVE = "NARRATIVE"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_type: EventType
    account_id: AccountId
    as_of: datetime
    occurred_at: datetime
    payload: str


def payload_json(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


class AuditLog:
    """Shares its connection with the store, so an event commits with the change it records."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def create(self) -> None:
        for statement in DDL:
            self._connection.execute(statement)

    def append(self, event: AuditEvent) -> None:
        self._connection.execute(
            INSERT_EVENT,
            (
                event.event_type,
                event.account_id,
                utc_text(event.as_of),
                utc_text(event.occurred_at),
                event.payload,
            ),
        )

    def history(self, account_id: AccountId, as_of: datetime) -> tuple[AuditEvent, ...]:
        rows = self._connection.execute(
            SELECT_HISTORY, (account_id, utc_text(as_of))
        ).fetchall()
        return tuple(
            AuditEvent(
                EventType(row[0]),
                AccountId(row[1]),
                as_of,
                datetime.fromisoformat(row[2]),
                row[3],
            )
            for row in rows
        )
