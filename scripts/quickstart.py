"""Set up the project and check it, in one command: python3 scripts/quickstart.py

Creates .venv if it's missing, installs the package and its dev tools with the virtual
environment's own interpreter, then runs scripts/verify.py with it. Standard library only.
"""

import sys

# First, so an older Python stops with this message before any 3.11-only code runs.
if sys.version_info < (3, 11):
    sys.exit(
        f"quickstart: Python 3.11 or newer is needed, but this is Python "
        f"{sys.version.split()[0]}. Run this with a newer python3."
    )

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENV = Path(".venv")


def say(text: str) -> None:
    print(text.encode("ascii", "replace").decode("ascii"), flush=True)


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def run(command: list[str]) -> bool:
    say("> " + " ".join(command))
    environment = dict(os.environ, PYTHONUTF8="1")
    executable = str(ROOT / command[0]) if command[0].startswith(".") else command[0]
    try:
        done = subprocess.run(
            [executable, *command[1:]], cwd=ROOT, env=environment, check=False
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
