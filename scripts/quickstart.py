"""Set up the project, check it and show it, in one command: python3 scripts/quickstart.py

Creates .venv if it's missing, installs the package and its dev tools with the virtual
environment's own interpreter, then runs scripts/verify.py with it. Once that passes, it replays
the golden day into an emptied out/demo/, so every quickstart shows the same run, and shows one
case. Standard library only.
"""

import sys

# First, so an older Python stops with this message before any 3.11-only code runs.
if sys.version_info < (3, 11):
    sys.exit(
        f"quickstart: Python 3.11 or newer is needed, but this is Python "
        f"{sys.version.split()[0]}. Run this with a newer python3."
    )

import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = Path(".venv")
DEMO = ROOT / "out" / "demo"
GOLDEN_DAY = (
    "tests/golden/payments.jsonl",
    "tests/golden/profiles.jsonl",
    "--as-of",
    "2026-11-03T00:00:00Z",
)
SHOWN_CASE = "ACC-101"


def say(text: str) -> None:
    print(text.encode("ascii", "replace").decode("ascii"), flush=True)


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def shown(path: Path) -> str:
    """A path inside the repository as it's typed from the root, any other path in full."""
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def run(command: list[str], stderr_to: Path | None = None) -> bool:
    say("> " + " ".join(command))
    environment = dict(os.environ, PYTHONUTF8="1")
    executable = str(ROOT / command[0]) if command[0].startswith(".") else command[0]
    try:
        if stderr_to is None:
            done = subprocess.run(
                [executable, *command[1:]], cwd=ROOT, env=environment, check=False
            )
        else:
            with stderr_to.open("w", encoding="utf-8") as log:
                done = subprocess.run(
                    [executable, *command[1:]],
                    cwd=ROOT,
                    env=environment,
                    stderr=log,
                    check=False,
                )
    except OSError as err:
        say(f"quickstart: couldn't start {command[0]}: {err}")
        return False
    if done.returncode != 0:
        say(
            f"quickstart: stopped, because that command failed (exit code {done.returncode})."
        )
        return False
    return True


def main() -> int:
    python = venv_python()
    if not (ROOT / VENV).exists():
        if not run([sys.executable, "-m", "venv", str(VENV)]):
            return 1
    elif not (ROOT / python).exists():
        say(
            f"quickstart: {VENV} exists but has no {python}. Delete {VENV} and run this again."
        )
        return 1
    steps = (
        [str(python), "-m", "pip", "install", "-c", "constraints.txt", "-e", ".[dev]"],
        [str(python), "scripts/verify.py"],
    )
    for command in steps:
        if not run(command):
            return 1
    say("quickstart: set up and checked.")
    return 0 if demo(str(python), DEMO) else 1


def demo(python: str, folder: Path) -> bool:
    """The golden day, replayed into an emptied folder, and one of its cases."""
    try:
        shutil.rmtree(folder)
    except FileNotFoundError:
        pass
    except OSError as err:
        say(f"quickstart: couldn't empty {shown(folder)}: {err}")
        return False
    folder.mkdir(parents=True)
    database, packs, log = (
        folder / "mule_triage.db",
        folder / "packs",
        folder / "run.log",
    )
    stored = ["--db", shown(database)]
    rerun = ["run", *GOLDEN_DAY, *stored, "--out", shown(packs)]
    case = ["--as-of", GOLDEN_DAY[3], *stored]
    say(f"\nquickstart: the golden day, replayed into {shown(folder)}:")
    if not run([python, "-m", "mule_triage", *rerun, "--ai", "replay"], log):
        return False
    say(f"\nquickstart: one case, {SHOWN_CASE}:")
    if not run([python, "-m", "mule_triage", "show", SHOWN_CASE, *case]):
        return False
    say(f"\nquickstart: evidence packs: {shown(packs)}")
    say(f"quickstart: run summary: {shown(packs / 'run_summary.json')}")
    say(f"quickstart: logs, as JSON lines: {shown(log)}")
    say("quickstart: to manually activate .venv:")
    say("  source .venv/bin/activate")
    say("quickstart: to try next, with .venv activated:")
    say(f"  python -m mule_triage show ACC-106 {' '.join(case)}")
    say(
        f"  python -m mule_triage dispose {SHOWN_CASE} {' '.join(case)} --version 1"
        " --reason ESCALATED --reviewer R-1 --narrative DISCARDED"
    )
    say(f"  python -m mule_triage {' '.join(rerun)}   (a re-run changes no case)")
    say("  python -m mule_triage run --help")
    return True


if __name__ == "__main__":
    sys.exit(main())
