"""Reads each input file's lines into accepted records and rejections. It never writes.

A rejection's reason names the field and pydantic's error type, never pydantic's message, which
quotes the rejected value. A key that isn't one of the model's fields is data too, so it isn't
named either.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from mule_triage.contracts import AccountProfile, Payment

RecordT = TypeVar("RecordT", bound=BaseModel)

WHOLE_RECORD = "record"
UNEXPECTED_FIELD = "unexpected field"


@dataclass(frozen=True, slots=True)
class Rejection:
    line_number: int
    record_id: str | None
    reason: str


@dataclass(frozen=True, slots=True)
class Accepted(Generic[RecordT]):
    line_number: int
    record: RecordT


@dataclass(frozen=True, slots=True)
class ParsedFile(Generic[RecordT]):
    received: int
    accepted: tuple[Accepted[RecordT], ...]
    rejections: tuple[Rejection, ...]


def read_lines(path: Path) -> tuple[str, ...]:
    with path.open(encoding="utf-8") as handle:
        return tuple(handle)


def parse_payments(lines: Iterable[str]) -> ParsedFile[Payment]:
    return parse_lines(lines, Payment, "transaction_id")


def parse_profiles(lines: Iterable[str]) -> ParsedFile[AccountProfile]:
    return parse_lines(lines, AccountProfile, "account_id")


def parse_lines(
    lines: Iterable[str], model: type[RecordT], id_field: str
) -> ParsedFile[RecordT]:
    received = 0
    accepted: list[Accepted[RecordT]] = []
    rejections: list[Rejection] = []
    for line_number, line in enumerate(lines, start=1):
        received = line_number
        try:
            record = model.model_validate_json(line)
        except ValidationError as error:
            rejections.append(
                Rejection(
                    line_number, record_id_in(line, id_field), reason_for(error, model)
                )
            )
        else:
            accepted.append(Accepted(line_number, record))
    return ParsedFile(received, tuple(accepted), tuple(rejections))


def record_id_in(line: str, id_field: str) -> str | None:
    try:
        data = json.loads(line)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    found = data.get(id_field)
    return found if isinstance(found, str) else None


def reason_for(error: ValidationError, model: type[BaseModel]) -> str:
    return "; ".join(
        f"{field_named(detail['loc'], model)}: {detail['type']}"
        for detail in error.errors()
    )


def field_named(location: tuple[int | str, ...], model: type[BaseModel]) -> str:
    if not location:
        return WHOLE_RECORD
    field = location[0]
    if isinstance(field, str) and field in model.model_fields:
        return field
    return UNEXPECTED_FIELD
