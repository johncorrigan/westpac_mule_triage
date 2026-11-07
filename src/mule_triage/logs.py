"""The log format: each record is one event of one run, as one line of JSON.

A module logs an event with `log_event`, and `cli.py` writes every record through `JsonLines` to
stderr, so stdout keeps the run summary. Fields hold counts, codes and durations, never record
content such as an account ID or a payment reference, because logs travel further than the packs.
"""

import json
import logging
from datetime import UTC, datetime

FIELDS = "fields"


def log_event(
    logger: str, level: int, event: str, run_id: int | None, **fields: object
) -> None:
    """`run_id` is None only before a run is stored, or when it wasn't."""
    logging.getLogger(logger).log(
        level, event, extra={FIELDS: {"run_id": run_id, **fields}}
    )


class JsonLines(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        moment = datetime.fromtimestamp(record.created, UTC)
        document: dict[str, object] = {
            "time": moment.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "run_id": None,
        }
        fields = getattr(record, FIELDS, None)
        if isinstance(fields, dict):
            document.update(fields)
        return json.dumps(document, ensure_ascii=True, separators=(",", ":"))
