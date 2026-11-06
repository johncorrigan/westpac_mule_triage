import argparse
import sys
import tomllib
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from enum import IntEnum
from pathlib import Path

from pydantic import ValidationError

from mule_triage import pipeline
from mule_triage.contracts import Alert, NoAlert, Outcome, RulesConfig, RunResult
from mule_triage.errors import ReconciliationError, RulesError
from mule_triage.pipeline import Settings
from mule_triage.rules import unreachable_settings

DEFAULT_RULES = Path("rules/rules-v1.toml")
DEFAULT_DATABASE = Path("out/mule_triage.db")
DEFAULT_OUT = Path("out/run")

Runner = Callable[[Path, Path, datetime, Settings], RunResult]


class ExitCode(IntEnum):
    SUCCESS = 0
    UNEXPECTED_ERROR = 1
    USAGE_ERROR = 2
    RECONCILIATION_FAILED = 3
    DEGRADED = 4


def aware_time(text: str) -> datetime:
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"isn't an ISO 8601 time: {text}") from error
    if moment.tzinfo is None:
        raise argparse.ArgumentTypeError(
            f"needs a timezone, such as 2026-11-03T00:00:00Z: {text}"
        )
    return moment.astimezone(UTC)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mule_triage",
        description=(
            "Triage possible money-mule accounts in one day of synthetic payments."
        ),
    )
    commands = parser.add_subparsers(dest="command")
    run = commands.add_parser(
        "run", help="ingest a day's files and give every account an outcome"
    )
    run.add_argument("payments", type=Path, help="payments, as JSON Lines")
    run.add_argument("profiles", type=Path, help="account profiles, as JSON Lines")
    run.add_argument(
        "--as-of",
        required=True,
        type=aware_time,
        help="the time the run is judged at, with a timezone",
    )
    run.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    run.add_argument("--db", type=Path, default=DEFAULT_DATABASE)
    run.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser


def load_rules(path: Path) -> RulesConfig:
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle, parse_float=Decimal)
    except OSError as error:
        raise RulesError(f"{path}: can't be read ({error.strerror})") from error
    except tomllib.TOMLDecodeError as error:
        raise RulesError(f"{path}: isn't valid TOML ({error})") from error
    try:
        rules = RulesConfig.model_validate(data)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in detail['loc']) or 'rules'}: "
            f"{detail['type']}"
            for detail in error.errors()
        )
        raise RulesError(f"{path}: {problems}") from error
    unreachable = unreachable_settings(rules)
    if unreachable:
        raise RulesError(f"{path}: {'; '.join(unreachable)}")
    return rules


def summary(result: RunResult) -> str:
    counts = result.payment_counts
    totals = Counter(account.outcome for account in result.accounts)
    lines = [
        f"payments: {counts.received} received, {counts.accepted} accepted,"
        f" {counts.rejected} rejected, {counts.duplicates} duplicates",
        ", ".join(f"{outcome}: {totals[outcome]}" for outcome in Outcome),
    ]
    for account in result.accounts:
        if isinstance(account, NoAlert):
            continue
        flags = [account.outcome.value]
        if isinstance(account, Alert):
            flags.append(account.priority.value)
            if account.incomplete:
                flags.append("INCOMPLETE")
        reasons = ",".join(sorted(account.reasons))
        lines.append(f"{account.account_id} {' '.join(flags)} {reasons}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, run: Runner = pipeline.run) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return ExitCode.SUCCESS
    try:
        rules = load_rules(args.rules)
    except RulesError as error:
        print(f"mule_triage: {error}", file=sys.stderr)
        return ExitCode.USAGE_ERROR
    try:
        args.db.parent.mkdir(parents=True, exist_ok=True)
        result = run(args.payments, args.profiles, args.as_of, Settings(args.db, rules))
    except ReconciliationError as error:
        print(f"mule_triage: nothing was stored: {error}", file=sys.stderr)
        return ExitCode.RECONCILIATION_FAILED
    except OSError as error:
        print(f"mule_triage: {error.filename}: {error.strerror}", file=sys.stderr)
        return ExitCode.USAGE_ERROR
    print(summary(result))
    return ExitCode.SUCCESS
