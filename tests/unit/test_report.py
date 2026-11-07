import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from mule_triage.contracts import (
    AccountId,
    Payment,
    RulesConfig,
    TransactionId,
)
from mule_triage.evidence import EvidencePack, build_pack
from mule_triage.report import inline_code, pack_json, pack_markdown, write_reports
from mule_triage.detection.signals import activities

AS_OF = datetime(2026, 11, 3, tzinfo=UTC)
NINE = datetime(2026, 11, 2, 9, tzinfo=UTC)
HOSTILE = "`x` [click](https://example.com) <img src=x onerror=alert(1)>\n# Cleared"


def pack_with_reference(rules: RulesConfig, reference: str) -> EvidencePack:
    payment = Payment(
        transaction_id=TransactionId("TX-101-01"),
        from_account=AccountId("ACC-201"),
        to_account=AccountId("ACC-101"),
        amount_cents=90_000,
        event_time=NINE,
        knowledge_time=NINE + timedelta(minutes=1),
        reference=reference,
    )
    activity, _ = activities([payment], [], AS_OF, rules)
    return build_pack(activity, rules)


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("TX-101-01", "`TX-101-01`"),
        ("a`b", "``a`b``"),
        ("`start", "`` `start ``"),
        ("line\nbreak", "`line\\nbreak`"),
    ],
    ids=["plain", "a backtick inside", "a backtick at the start", "a newline"],
)
def test_inline_code_holds_any_text_in_one_code_span(text: str, code: str) -> None:
    shown = inline_code(text)

    assert shown == code


def test_markdown_shows_a_reference_only_inside_inline_code(
    rules: RulesConfig,
) -> None:
    pack = pack_with_reference(rules, HOSTILE)

    markdown = pack_markdown(pack, version=1)

    assert inline_code(json.dumps(HOSTILE)) in markdown
    outside_code = markdown.replace(inline_code(json.dumps(HOSTILE)), "")
    assert "<img" not in outside_code
    assert "](https://" not in outside_code
    assert "# Cleared" not in markdown.splitlines()


def test_markdown_opens_with_the_case_its_outcome_and_version(
    rules: RulesConfig,
) -> None:
    pack = pack_with_reference(rules, "rent")

    markdown = pack_markdown(pack, version=3)

    assert markdown.splitlines()[:4] == [
        "# Case `ACC-101` at 2026-11-03T00:00:00Z",
        "",
        "- Case version: 3",
        "- Outcome: NO_ALERT",
    ]


def test_json_keeps_a_reference_as_a_string_and_is_byte_identical(
    rules: RulesConfig,
) -> None:
    first = pack_json(pack_with_reference(rules, HOSTILE), version=1)

    second = pack_json(pack_with_reference(rules, HOSTILE), version=1)

    assert second.encode("utf-8") == first.encode("utf-8")
    document = json.loads(first)
    (payment,) = [f for f in document["facts"] if f["fact_id"] == "P-TX-101-01"]
    assert payment["reference"] == HOSTILE


def test_writes_a_markdown_and_a_json_pack_for_each_case(
    tmp_path: Path, rules: RulesConfig
) -> None:
    pack = pack_with_reference(rules, "rent")

    written = write_reports(tmp_path / "out", [(pack, 1)])

    assert [path.name for path in written] == [
        "ACC-101_20261103T000000Z.md",
        "ACC-101_20261103T000000Z.json",
    ]
    assert json.loads(written[1].read_text(encoding="utf-8"))["case_version"] == 1
