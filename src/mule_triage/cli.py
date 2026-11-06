import argparse
from collections.abc import Sequence


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
    return 0
