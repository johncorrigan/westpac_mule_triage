from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from mule_triage.cli import (
    DEFAULT_RULES,
    ExitCode,
    build_parser,
    load_rules,
    main,
)
from mule_triage.contracts import RulesConfig, RunResult
from mule_triage.errors import ReconciliationError, RulesError, StoreError
from mule_triage.pipeline import Settings

RULES_V1 = Path(__file__).resolve().parents[2] / "rules" / "rules-v1.toml"
AS_OF = "2026-11-03T00:00:00Z"


def never_runs(
    payments: Path, profiles: Path, as_of: datetime, settings: Settings
) -> RunResult:
    raise AssertionError("the run started")


def unbalanced(
    payments: Path, profiles: Path, as_of: datetime, settings: Settings
) -> RunResult:
    raise ReconciliationError("payments: received 3 lines, but stored 2")


def old_database(
    payments: Path, profiles: Path, as_of: datetime, settings: Settings
) -> RunResult:
    raise StoreError("triage.db: has schema version 0, not 1")


def run_args(tmp_path: Path, rules: Path) -> list[str]:
    return [
        "run",
        "payments.jsonl",
        "profiles.jsonl",
        "--as-of",
        AS_OF,
        "--rules",
        str(rules),
        "--db",
        str(tmp_path / "triage.db"),
    ]


def test_prints_usage_and_succeeds_when_given_no_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main([])

    assert exit_code == ExitCode.SUCCESS
    assert capsys.readouterr().out.startswith("usage: mule_triage")


def test_a_usage_error_exits_with_the_usage_error_code() -> None:
    with pytest.raises(SystemExit) as raised:
        main(["--no-such-option"])

    assert raised.value.code == ExitCode.USAGE_ERROR


def test_the_exit_codes_keep_their_documented_values() -> None:
    codes = {code.name: int(code) for code in ExitCode}

    assert codes == {
        "SUCCESS": 0,
        "UNEXPECTED_ERROR": 1,
        "USAGE_ERROR": 2,
        "RECONCILIATION_FAILED": 3,
        "DEGRADED": 4,
    }


def test_loads_rules_v1_with_the_readmes_thresholds() -> None:
    rules = load_rules(RULES_V1)

    assert rules == RulesConfig.model_validate(
        {
            "version": "v1",
            "window_hours": 24,
            "fan_in": {"min_senders": 5},
            "pass_through": {"min_ratio": Decimal("0.80"), "max_hours": 12},
            "new_account": {"younger_than_days": 30},
            "outcome": {"alert_at": 2, "high_priority_at": 3},
            "evidence": {"max_counterparties": 5},
        }
    )


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (("min_senders = 5", "min_senders = 5\nmax_senders = 9"), "extra_forbidden"),
        (("min_senders = 5", 'min_senders = "5"'), "int_type"),
        (("min_ratio = 0.80", "min_ratio = 0"), "is_instance_of"),
        (("max_hours = 12", "max_hours = 0"), "greater_than"),
        (("max_counterparties = 5", "max_counterparties = 0"), "greater_than_equal"),
        (("younger_than_days = 30\n", ""), "missing"),
        (("high_priority_at = 3", "high_priority_at = 1"), "value_error"),
        (("[fan_in]", "[fan_in"), "isn't valid TOML"),
        (("high_priority_at = 3", "high_priority_at = 4"), "above the 3 signals"),
        (
            (
                "alert_at = 2\nhigh_priority_at = 3",
                "alert_at = 4\nhigh_priority_at = 4",
            ),
            "alert_at: 4 is above the 3 signals",
        ),
    ],
    ids=[
        "unknown setting",
        "text for a number",
        "a whole number for the ratio",
        "zero hours",
        "no counterparties",
        "missing setting",
        "high priority below an alert",
        "invalid TOML",
        "high priority out of reach",
        "alert out of reach",
    ],
)
def test_refuses_a_rules_file_with_a_bad_setting(
    tmp_path: Path, change: tuple[str, str], problem: str
) -> None:
    text = RULES_V1.read_text(encoding="utf-8")
    assert change[0] in text
    bad = tmp_path / "rules.toml"
    bad.write_text(text.replace(change[0], change[1]), encoding="utf-8")

    with pytest.raises(RulesError, match=problem):
        load_rules(bad)


def test_a_bad_rules_file_stops_the_run_with_the_usage_error_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "rules.toml"
    bad.write_text("version = 'v1'\n", encoding="utf-8")

    exit_code = main(run_args(tmp_path, bad), run=never_runs)

    assert exit_code == ExitCode.USAGE_ERROR
    assert "window_hours: missing" in capsys.readouterr().err


def test_a_missing_rules_file_stops_the_run_with_the_usage_error_code(
    tmp_path: Path,
) -> None:
    exit_code = main(run_args(tmp_path, tmp_path / "absent.toml"), run=never_runs)

    assert exit_code == ExitCode.USAGE_ERROR


def test_a_run_that_doesnt_reconcile_exits_with_the_reconciliation_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(run_args(tmp_path, RULES_V1), run=unbalanced)

    assert exit_code == ExitCode.RECONCILIATION_FAILED
    assert "payments: received 3 lines" in capsys.readouterr().err


def test_a_database_with_another_schema_exits_with_the_usage_error_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(run_args(tmp_path, RULES_V1), run=old_database)

    assert exit_code == ExitCode.USAGE_ERROR
    assert "schema version 0" in capsys.readouterr().err


@pytest.mark.parametrize(
    "as_of",
    ["2026-11-03T00:00:00", "3 November 2026"],
    ids=["no timezone", "not ISO 8601"],
)
def test_refuses_an_as_of_time_without_a_timezone(tmp_path: Path, as_of: str) -> None:
    args = run_args(tmp_path, RULES_V1)
    args[args.index(AS_OF)] = as_of

    with pytest.raises(SystemExit) as raised:
        main(args, run=never_runs)

    assert raised.value.code == ExitCode.USAGE_ERROR


def test_the_run_command_defaults_to_rules_v1_and_writes_under_out() -> None:
    args = build_parser().parse_args(["run", "p.jsonl", "q.jsonl", "--as-of", AS_OF])

    assert args.rules == DEFAULT_RULES == Path("rules/rules-v1.toml")
    assert args.db.parts[0] == "out"
    assert args.out.parts[0] == "out"
    assert args.as_of == datetime(2026, 11, 3, tzinfo=UTC)
