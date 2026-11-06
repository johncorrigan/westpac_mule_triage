import argparse
from collections.abc import Sequence
from enum import IntEnum


class ExitCode(IntEnum):
    SUCCESS = 0
    UNEXPECTED_ERROR = 1
    USAGE_ERROR = 2
    RECONCILIATION_FAILED = 3
    DEGRADED = 4


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="mule_triage",
        description=(
            "Triage possible money-mule accounts in one day of synthetic payments."
        ),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return ExitCode.SUCCESS
