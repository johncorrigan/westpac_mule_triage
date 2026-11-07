import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from mule_triage.cli import ExitCode, main
from mule_triage.clock import FixedClock
from mule_triage.contracts import DEFAULT_MODEL, RulesConfig
from mule_triage.narrative.client import Drafted, DraftResult, DraftSource, ModelLabel
from mule_triage.narrative.prompt import Prompt
from mule_triage.pipeline import Settings, run

GOLDEN = Path(__file__).resolve().parents[1] / "golden"
AS_OF = "2026-11-03T00:00:00Z"
CLOCK = FixedClock(datetime(2026, 11, 8, 3, 30, tzinfo=UTC))
LOOK_ALIKE_REPLY = json.dumps(
    {
        "sentences": [
            {
                "text": "ACC-101 was paid by \uff17 dis\u200btinct senders, at least 5 needed.",
                "fact_ids": ["S-FAN_IN"],
            }
        ]
    }
)


@pytest.fixture
def database(tmp_path: Path) -> Path:
    database = tmp_path / "triage.db"
    payments, profiles = GOLDEN / "payments.jsonl", GOLDEN / "profiles.jsonl"
    args = ["run", str(payments), str(profiles), "--as-of", AS_OF]
    exit_code = main(
        [*args, "--db", str(database), "--out", str(tmp_path / "out")], clock=CLOCK
    )
    assert exit_code == ExitCode.SUCCESS
    return database


@dataclass(frozen=True, slots=True)
class LookAlikeClient:
    """Answers every prompt with a full-width 7 and a zero-width space in its sentence."""

    def draft(self, prompt: Prompt, facts_sha256: str) -> DraftResult:
        label = ModelLabel(DEFAULT_MODEL, "sha256:5f2a9c", DraftSource.LIVE)
        return Drafted(LOOK_ALIKE_REPLY, label, latency_seconds=None)


def show_args(database: Path, account: str) -> list[str]:
    return ["show", account, "--as-of", AS_OF, "--db", str(database)]


def narrative_section(shown: str) -> str:
    return shown.partition("## Narrative\n")[2].partition("## Audit history")[0]


def dispose_args(database: Path, version: int) -> list[str]:
    return [
        "dispose",
        "ACC-101",
        "--as-of",
        AS_OF,
        "--db",
        str(database),
        "--version",
        str(version),
        "--reason",
        "FALSE_POSITIVE",
        "--reviewer",
        "REV-1",
        "--narrative",
        "DISCARDED",
    ]


def test_show_displays_one_case_with_its_version_and_history(
    database: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()

    exit_code = main(["show", "ACC-101", "--as-of", AS_OF, "--db", str(database)])

    shown = capsys.readouterr().out
    assert exit_code == ExitCode.SUCCESS
    assert shown.splitlines()[0] == "Case status: OPEN, version 1"
    assert "5 of 9 shown; 4 left out" in shown
    assert "CASE_OPENED" in shown
    assert shown.isascii()


def test_show_gives_a_needs_review_case_the_templates_note_and_sentences(
    database: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()

    exit_code = main(show_args(database, "ACC-105"))

    narrative = narrative_section(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert "no AI draft: write this one manually" in narrative
    assert "`ACC-105 is NEEDS_REVIEW, under rules v1.`" in narrative


def test_show_gives_an_accepted_draft_as_the_validator_normalised_it(
    tmp_path: Path, rules: RulesConfig, capsys: pytest.CaptureFixture[str]
) -> None:
    database = tmp_path / "triage.db"
    settings = Settings(database, rules, tmp_path / "out", CLOCK, LookAlikeClient())
    as_of = datetime(2026, 11, 3, tzinfo=UTC)
    run(GOLDEN / "payments.jsonl", GOLDEN / "profiles.jsonl", as_of, settings)
    capsys.readouterr()

    exit_code = main(show_args(database, "ACC-101"))

    narrative = narrative_section(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert "`sha256:5f2a9c`" in narrative
    assert "`ACC-101 was paid by 7 distinct senders, at least 5 needed.`" in narrative
    assert "\\uff17" not in narrative and "\\u200b" not in narrative


def test_show_has_no_narrative_for_a_version_newer_than_its_last_narrative(
    database: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(dispose_args(database, version=1), clock=CLOCK)
    capsys.readouterr()

    exit_code = main(show_args(database, "ACC-101"))

    narrative = narrative_section(capsys.readouterr().out)
    assert exit_code == ExitCode.SUCCESS
    assert "no narrative for this version" in narrative
    assert "no AI draft" not in narrative


def test_dispose_closes_the_case_at_the_version_the_reviewer_saw(
    database: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()

    exit_code = main(dispose_args(database, version=1), clock=CLOCK)

    assert exit_code == ExitCode.SUCCESS
    assert "closed at version 2" in capsys.readouterr().out


def test_dispose_on_an_old_version_tells_the_reviewer_to_re_read_the_case(
    database: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    main(dispose_args(database, version=1), clock=CLOCK)

    exit_code = main(dispose_args(database, version=1), clock=CLOCK)

    assert exit_code == ExitCode.STALE_VERSION
    assert "re-read the case" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["run", "show", "dispose"])
def test_a_database_sqlite_cant_use_stops_with_a_message_not_a_traceback(
    tmp_path: Path, command: str, capsys: pytest.CaptureFixture[str]
) -> None:
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"not a database, " * 64)
    args = {
        "run": [
            "run",
            str(GOLDEN / "payments.jsonl"),
            str(GOLDEN / "profiles.jsonl"),
            "--as-of",
            AS_OF,
            "--db",
            str(broken),
            "--out",
            str(tmp_path / "out"),
        ],
        "show": ["show", "ACC-101", "--as-of", AS_OF, "--db", str(broken)],
        "dispose": dispose_args(broken, version=1),
    }[command]

    exit_code = main(args, clock=CLOCK)

    assert exit_code == ExitCode.DATABASE_UNAVAILABLE
    assert "nothing was changed" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["show", "dispose"])
def test_a_missing_database_is_a_usage_error_and_isnt_created(
    tmp_path: Path, command: str
) -> None:
    absent = tmp_path / "absent.db"
    args = dispose_args(absent, version=1)
    if command == "show":
        args = ["show", "ACC-101", "--as-of", AS_OF, "--db", str(absent)]

    exit_code = main(args, clock=CLOCK)

    assert exit_code == ExitCode.USAGE_ERROR
    assert not absent.exists()
