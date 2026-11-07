import argparse
import re
import sqlite3
import sys
import tomllib
from collections import Counter
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from enum import IntEnum
from pathlib import Path
from typing import assert_never

from pydantic import ValidationError

from mule_triage import pipeline
from mule_triage.clock import Clock
from mule_triage.contracts import (
    ACCOUNT_ID_PATTERN,
    AiMode,
    AiSettings,
    Alert,
    NarrativeOutcome,
    NoAlert,
    Outcome,
    RulesConfig,
    RunResult,
)
from mule_triage.errors import (
    ReconciliationError,
    RulesError,
    SettingsError,
    StoreError,
)
from mule_triage.narrative.client import (
    LiveClient,
    ModelClient,
    OffClient,
    ReplayClient,
)
from mule_triage.pipeline import Settings
from mule_triage.report import case_markdown
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
from mule_triage.detection.rules import unreachable_settings
from mule_triage.records.store import open_store

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
    STALE_VERSION = 5
    DATABASE_UNAVAILABLE = 6


class WallClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def plain_id(text: str) -> str:
    if re.fullmatch(ACCOUNT_ID_PATTERN, text) is None:
        raise argparse.ArgumentTypeError(
            f"must be up to 64 letters, digits or _ . : -: {text!r}"
        )
    return text


def case_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("account", type=plain_id, help="the case's account ID")
    parser.add_argument(
        "--as-of", required=True, type=aware_time, help="the case's as-of time"
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DATABASE)


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
    ai_arguments(run)
    show = commands.add_parser(
        "show", help="show one case, its version, its evidence and its history"
    )
    case_arguments(show)
    show.add_argument("--rules", type=Path, default=DEFAULT_RULES)
    disposal = commands.add_parser(
        "dispose", help="record a reviewer's disposition of one case"
    )
    case_arguments(disposal)
    disposal.add_argument(
        "--version",
        required=True,
        type=int,
        help="the case version the reviewer saw, as `show` gives it",
    )
    disposal.add_argument(
        "--reason", required=True, type=DispositionReason, choices=DispositionReason
    )
    disposal.add_argument("--reviewer", required=True, type=plain_id)
    disposal.add_argument(
        "--narrative",
        required=True,
        type=NarrativeUse,
        choices=NarrativeUse,
        help="whether the reviewer accepted, edited or discarded the narrative",
    )
    return parser


def ai_arguments(parser: argparse.ArgumentParser) -> None:
    defaults = AiSettings()
    parser.add_argument(
        "--ai",
        type=AiMode,
        choices=list(AiMode),
        default=defaults.mode,
        help="draft narratives with no model, from recorded replies, or live",
    )
    parser.add_argument(
        "--record",
        action="store_true",
        help="with --ai live, save each reply under its key in --recordings",
    )
    parser.add_argument("--model", default=defaults.model)
    parser.add_argument("--endpoint", default=defaults.endpoint, help="Ollama's URL")
    parser.add_argument("--ai-timeout", type=int, default=defaults.timeout_seconds)
    parser.add_argument("--recordings", type=Path, default=defaults.recordings)


def ai_settings(args: argparse.Namespace) -> AiSettings:
    try:
        return AiSettings(
            mode=args.ai,
            model=args.model,
            endpoint=args.endpoint,
            timeout_seconds=args.ai_timeout,
            record=args.record,
            recordings=args.recordings,
        )
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in detail['loc']) or 'ai'}: {detail['msg']}"
            for detail in error.errors()
        )
        raise SettingsError(f"AI settings: {problems}") from error


def model_client(ai: AiSettings) -> ModelClient:
    match ai.mode:
        case AiMode.OFF:
            return OffClient()
        case AiMode.REPLAY:
            return ReplayClient(ai.recordings, ai.model)
        case AiMode.LIVE:
            record_to = ai.recordings if ai.record else None
            return LiveClient(ai.endpoint, ai.model, ai.timeout_seconds, record_to)
        case _:
            assert_never(ai.mode)


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
    narratives = Counter(each.outcome for each in result.narratives)
    lines.append(
        "narratives: "
        + ", ".join(f"{outcome}: {narratives[outcome]}" for outcome in NarrativeOutcome)
    )
    lines += [
        f"{each.account_id} live model call: {each.latency_seconds:.2f} s"
        for each in result.narratives
        if each.latency_seconds is not None
    ]
    return "\n".join(lines)


def unavailable_warning(result: RunResult) -> str | None:
    count = sum(
        each.outcome is NarrativeOutcome.AI_UNAVAILABLE for each in result.narratives
    )
    if count == 0:
        return None
    templates = "narrative is a template" if count == 1 else "narratives are templates"
    return (
        f"mule_triage: {count} {templates} because the model was unavailable"
        " (AI_UNAVAILABLE); the run is stored"
    )


def dispose_command(args: argparse.Namespace, clock: Clock) -> int:
    disposition = Disposition(
        account_id=args.account,
        as_of=args.as_of,
        seen_version=args.version,
        reason=args.reason,
        reviewer=args.reviewer,
        narrative=args.narrative,
    )
    with open_store(args.db) as store:
        result = dispose(store, disposition, clock)
    case = f"{args.account} at {args.as_of.isoformat()}"
    match result:
        case Disposed(version=version):
            print(f"{case}: {args.reason}, and closed at version {version}")
            return ExitCode.SUCCESS
        case Stale():
            print(f"mule_triage: {case}: {result.message}", file=sys.stderr)
            return ExitCode.STALE_VERSION
        case CaseNotFound():
            print(f"mule_triage: {case}: no such case", file=sys.stderr)
            return ExitCode.USAGE_ERROR
        case _:
            assert_never(result)


def show_command(args: argparse.Namespace, rules: RulesConfig) -> int:
    with open_store(args.db) as store:
        view = case_view(store, args.account, args.as_of, rules)
    if view is None:
        case = f"{args.account} at {args.as_of.isoformat()}"
        print(f"mule_triage: {case}: no such case", file=sys.stderr)
        return ExitCode.USAGE_ERROR
    print(case_markdown(view), end="")
    return ExitCode.SUCCESS


def main(
    argv: Sequence[str] | None = None,
    run: Runner = pipeline.run,
    clock: Clock | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return ExitCode.SUCCESS
    now = WallClock() if clock is None else clock
    try:
        return dispatch(args, run, now)
    except sqlite3.DatabaseError as error:
        print(
            f"mule_triage: {args.db}: {error}; nothing was changed, so try again once"
            " the database is free, or use another --db",
            file=sys.stderr,
        )
        return ExitCode.DATABASE_UNAVAILABLE


def dispatch(args: argparse.Namespace, run: Runner, now: Clock) -> int:
    """A locked or unreadable database raises sqlite3.DatabaseError, which `main` reports;
    each command's transaction has rolled back by then."""
    if args.command in ("dispose", "show") and not args.db.is_file():
        print(f"mule_triage: {args.db}: no database there", file=sys.stderr)
        return ExitCode.USAGE_ERROR
    try:
        if args.command == "dispose":
            return dispose_command(args, now)
        rules = load_rules(args.rules)
        if args.command == "show":
            return show_command(args, rules)
        ai = ai_settings(args)
    except (RulesError, SettingsError, StoreError) as error:
        print(f"mule_triage: {error}", file=sys.stderr)
        return ExitCode.USAGE_ERROR
    try:
        args.db.parent.mkdir(parents=True, exist_ok=True)
        settings = Settings(args.db, rules, args.out, now, model_client(ai))
        result = run(args.payments, args.profiles, args.as_of, settings)
    except ReconciliationError as error:
        print(f"mule_triage: nothing was stored: {error}", file=sys.stderr)
        return ExitCode.RECONCILIATION_FAILED
    except StoreError as error:
        print(f"mule_triage: {error}", file=sys.stderr)
        return ExitCode.USAGE_ERROR
    except OSError as error:
        print(f"mule_triage: {error.filename}: {error.strerror}", file=sys.stderr)
        return ExitCode.USAGE_ERROR
    print(summary(result))
    warning = unavailable_warning(result)
    if warning is not None:
        print(warning, file=sys.stderr)
        return ExitCode.DEGRADED
    return ExitCode.SUCCESS
