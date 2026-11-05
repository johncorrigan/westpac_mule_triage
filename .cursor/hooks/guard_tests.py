#!/usr/bin/env python3
"""Tests for guard.py: runs it against sample hook events, in a throwaway sandbox repository.

Usage: python3 .cursor/hooks/guard_tests.py [path/to/guard.py] [python-to-run-the-guard-with]

Run it after any change to guard.py; CI runs it too. It prints one line per case, ends with the
counts, and exits non-zero if any case fails. Standard library only, Python 3.8 or newer. The
file name keeps it out of pytest's default collection: it's a script, not part of the product's
test suite.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TRAILED = 'git commit -m "x\n\nAssisted-by: Cursor (Claude Opus 5.5)"'
HEREDOC_TRAILED = (
    "git commit -m \"$(cat <<'EOF'\nAdd x; tidy | up\n\n"
    "Assisted-by: Cursor (Claude Opus 5.5)\nEOF\n)\""
)
HEREDOC_PUSH_WORDS = (
    "git commit -m \"$(cat <<'EOF'\nExplain why we never git push\n\n"
    "Assisted-by: Cursor (Claude Opus 5.5)\nEOF\n)\""
)


def default_guard() -> Path:
    """guard.py beside this file in a repository, or in the kit's cursor/hooks/ folder."""
    for candidate in (HERE / "guard.py", HERE.parent / "cursor" / "hooks" / "guard.py"):
        if candidate.exists():
            return candidate
    return HERE / "guard.py"


def tool(name: str, **tool_input: object) -> dict:
    return {"hook_event_name": "preToolUse", "tool_name": name, "tool_input": tool_input}


class Harness:
    def __init__(self, guard: Path, python: str) -> None:
        self.sandbox = Path(tempfile.mkdtemp(prefix="guard-sandbox-")).resolve()
        self.root = str(self.sandbox)
        self.python = python
        self.cases = 0
        self.failures = 0
        hooks = self.sandbox / ".cursor" / "hooks"
        hooks.mkdir(parents=True)
        shutil.copy(str(guard), str(hooks / "guard.py"))
        self.git("init", "-q")

    def git(self, *args: str) -> None:
        identity = ["-c", "user.name=Test", "-c", "user.email=test@example.com"]
        subprocess.run(["git", *identity, *args], cwd=self.root, check=True)

    def at(self, rel: str) -> str:
        return self.root + "/" + rel

    def shell(self, command: str, cwd: str = "") -> dict:
        event = "beforeShellExecution"
        return {"hook_event_name": event, "command": command, "cwd": cwd or self.root}

    def run(self, hook: str, payload: object, raw: str = "", cwd: str = "") -> tuple:
        done = subprocess.run(
            [self.python, str(self.sandbox / ".cursor" / "hooks" / "guard.py"), hook],
            cwd=cwd or self.root,
            input=raw or json.dumps(payload),
            capture_output=True,
            text=True,
        )
        try:
            decision = json.loads(done.stdout)
        except ValueError:
            decision = {"permission": "UNPARSEABLE", "stdout": done.stdout, "stderr": done.stderr}
        return done.returncode, decision

    def check(self, label: str, hook: str, payload: object, expected: str, rule: str = "",
              raw: str = "", code: int = -1, cwd: str = "") -> None:
        self.cases += 1
        returncode, decision = self.run(hook, payload, raw, cwd)
        got = decision.get("permission")
        rule_ok = not rule or "({})".format(rule) in decision.get("user_message", "")
        ok = got == expected and rule_ok and (code < 0 or returncode == code)
        if not ok:
            self.failures += 1
        shown = "{} {}".format(got, "({})".format(rule) if rule else "")
        status = "ok" if ok else "FAIL"
        print("{:4} {:62} expected {:5} got {}".format(status, label, expected, shown))
        if not ok:
            print("     ", decision)


def tool_cases(h: Harness) -> None:
    print("== tool hook")
    h.check("Write into the oracle (absolute path)", "tool",
            tool("Write", path=h.at("tests/golden/expected/x.toml"), contents="x"),
            "deny", "oracle")
    h.check("Edit verify.py whose new text names the oracle", "tool",
            tool("StrReplace", path=h.at("scripts/verify.py"), old_string="a",
                 new_string="tests/golden/expected/SHA256SUMS"), "allow")
    h.check("Read the answer key", "tool",
            tool("Read", path=h.at("tests/golden/expected/outcomes.toml")), "allow")
    h.check("Read .env", "tool", tool("Read", path=h.at(".env")), "deny", "secrets")
    h.check("Write .cursor/hooks.json (relative)", "tool",
            tool("Write", path=".cursor/hooks.json", contents="{}"), "deny", "kit")
    h.check("Delete AGENTS.md", "tool", tool("Delete", path="AGENTS.md"), "deny", "kit")
    h.check("Write product code mentioning AGENTS.md and .env", "tool",
            tool("Write", path="src/pkg/x.py", contents="AGENTS.md .env"), "allow")
    h.check("Shell tool is left to the shell hook", "tool",
            tool("Shell", command="git push"), "allow")
    h.check("search_replace counts as a write", "tool",
            tool("search_replace", file_path="tests/golden/expected/a"), "deny", "oracle")
    h.check("Unknown tool with a target_file in the oracle", "tool",
            {"tool_input": {"target_file": "tests/golden/expected/b"}}, "deny", "oracle")
    h.check("Sibling folder expected_other is fine", "tool",
            tool("Write", path="tests/golden/expected_other/x"), "allow")
    h.check("Traversal back into the oracle", "tool",
            tool("Write", path="src/../tests/golden/expected/x"), "deny", "oracle")
    h.check("Write outside the repository", "tool",
            tool("Write", path="/tmp/elsewhere/tests/golden/expected/x"), "allow")
    h.check("Write into .git", "tool", tool("Write", path=".git/config"), "deny", "git-internals")
    h.check("Write a .env file", "tool",
            tool("Write", path="config/.env.local", contents="K=V"), "deny", "secrets")
    h.check("Write .env.example", "tool",
            tool("Write", path=".env.example", contents="K="), "allow")
    h.check("TodoWrite has no paths", "tool",
            tool("TodoWrite", todos=[{"content": "tests/golden/expected"}]), "allow")
    h.check("EditNotebook in the oracle", "tool",
            tool("EditNotebook", target_notebook="tests/golden/expected/n.ipynb"), "deny", "oracle")
    h.check("Letter case: tests/Golden/Expected/outcomes.toml", "tool",
            tool("Write", path="tests/Golden/Expected/outcomes.toml"), "deny", "oracle")
    h.check("Write the guard's log in .git", "tool",
            tool("Write", path=".git/cursor-guard/controls.log", contents=""), "deny",
            "git-internals")
    h.check("Write a recorded reply", "tool",
            tool("Write", path="tests/ai/recorded/r1.json", contents="{}"), "deny", "recordings")
    h.check("Write .cursorignore", "tool",
            tool("Write", path=".cursorignore", contents=""), "deny", "kit")
    h.check("Write evidence/design.md", "tool",
            tool("Write", path="evidence/design.md", contents="x"), "allow")
    h.check("Hook started in the parent folder", "tool",
            tool("Write", path=h.at("tests/golden/expected/x")), "deny", "oracle",
            cwd=str(h.sandbox.parent))


def read_cases(h: Harness) -> None:
    print("== read hook")
    h.check("Read .env with content", "read",
            {"file_path": h.at(".env"), "content": "SECRET=1"}, "deny")
    h.check("Read README whose content mentions .env", "read",
            {"file_path": h.at("README.md"), "content": "see .env"}, "allow")
    h.check("Read .env.example", "read", {"file_path": h.at(".env.example")}, "allow")
    h.check("Attachment that is .env", "read",
            {"attachments": [{"file_path": ".env.prod"}]}, "deny")


def git_cases(h: Harness) -> None:
    print("== shell hook: Git")
    s = h.shell
    h.check("git push", "shell", s("git push origin main"), "deny", "push")
    h.check("git -C . push", "shell", s("git -C . push"), "deny", "push")
    h.check("X=1 git push", "shell", s("X=1 git push"), "deny", "push")
    h.check("git status && git push", "shell", s("git status && git push"), "deny", "push")
    h.check("commit without trailer", "shell", s('git commit -m "Add x"'), "deny", "trailer")
    h.check("commit with trailer, no verify.py yet", "shell", s(TRAILED), "allow")
    h.check("add then commit without trailer", "shell",
            s('git add -A && git commit -m "x"'), "deny", "trailer")
    h.check("heredoc commit with trailer", "shell", s(HEREDOC_TRAILED), "allow")
    h.check("heredoc message that mentions git push", "shell", s(HEREDOC_PUSH_WORDS), "allow")
    h.check("message with 'replace' and 'reset'", "shell",
            s('git commit -m "Replace the reset logic\n\nAssisted-by: Cursor (Claude Opus 5.5)"'),
            "allow")
    h.check("commit --amend", "shell",
            s('git commit --amend -m "x Assisted-by: y"'), "deny", "history")
    h.check("commit --no-verify", "shell",
            s('git commit --no-verify -m "Assisted-by: y"'), "deny", "checks")
    h.check("commit --date", "shell",
            s('git commit --date="2026-11-07T09:00" -m "x Assisted-by: y"'), "deny", "identity")
    h.check("commit --author", "shell",
            s('git commit --author="X <x@y>" -m "x Assisted-by: y"'), "deny", "identity")
    h.check("GIT_AUTHOR_DATE=... git commit", "shell",
            s('GIT_AUTHOR_DATE=2026-01-01 git commit -m "Assisted-by: y"'), "deny", "identity")
    h.check("git -c user.email=... commit", "shell",
            s('git -c user.email=x@y commit -m "Assisted-by: y"'), "deny", "git-config")
    h.check("git -c alias.p=push p", "shell", s("git -c alias.p=push p"), "deny", "git-config")
    h.check("reset --hard", "shell", s("git reset --hard HEAD~1"), "deny", "history")
    h.check("rebase", "shell", s("git rebase -i HEAD~3"), "deny", "history")
    h.check("git checkout over the oracle", "shell",
            s("git checkout -- tests/golden/expected/outcomes.toml"), "deny", "oracle")
    h.check("git restore AGENTS.md", "shell", s("git restore AGENTS.md"), "deny", "kit")
    h.check("git add the answer key", "shell",
            s("git add tests/golden/expected/outcomes.toml"), "allow")
    h.check("git branch (list)", "shell", s("git branch"), "allow")
    h.check("git branch feature", "shell", s("git branch feature"), "deny", "branches")
    h.check("git checkout -b", "shell", s("git checkout -b x"), "deny", "branches")
    h.check("git config user.email", "shell", s("git config user.email x@y"), "deny", "git-config")
    h.check("git config --get user.email", "shell", s("git config --get user.email"), "allow")
    h.check("gh repo create --push", "shell",
            s("gh repo create mine --public --source . --push"), "ask", "github")


def wrapper_cases(h: Harness) -> None:
    print("== shell hook: wrapped commands")
    s = h.shell
    h.check("bash -c 'git push'", "shell", s("bash -c 'git push'"), "deny", "push")
    h.check("env git push", "shell", s("env git push"), "deny", "push")
    h.check("command git push", "shell", s("command git push"), "deny", "push")
    h.check("time git push", "shell", s("time git push"), "deny", "push")
    h.check("nohup git push &", "shell", s("nohup git push &"), "deny", "push")
    h.check('echo "$(git push)"', "shell", s('echo "$(git push)"'), "deny", "push")
    h.check("sh -c 'cat .env'", "shell", s("sh -c 'cat .env'"), "deny", "secrets")
    h.check("bash -lc 'cd tests/golden && rm expected/x'", "shell",
            s("bash -lc 'cd tests/golden && rm expected/x'"), "deny", "oracle")
    h.check("bash -c 'python -m pytest -q'", "shell", s("bash -c 'python -m pytest -q'"), "allow")
    h.check("env with an assignment", "shell", s("env FOO=1 python x.py"), "allow")
    h.check("bare env", "shell", s("env"), "deny", "secrets")
    h.check("printenv", "shell", s("printenv"), "deny", "secrets")


def path_cases(h: Harness) -> None:
    print("== shell hook: protected paths")
    s = h.shell
    h.check("redirect into the oracle", "shell",
            s("echo hi > tests/golden/expected/x"), "deny", "oracle")
    h.check("glued redirect into the oracle", "shell",
            s("echo hi>tests/golden/expected/x"), "deny", "oracle")
    h.check("letter case: TESTS/golden/expected", "shell",
            s("echo x > TESTS/golden/expected/x"), "deny", "oracle")
    h.check("doubled slash", "shell", s("echo x > tests//golden/expected/x"), "deny", "oracle")
    h.check("dot segment", "shell", s("echo x > tests/golden/./expected/x"), "deny", "oracle")
    h.check("cd, then a relative write", "shell",
            s("cd tests/golden && echo x > expected/outcomes.toml"), "deny", "oracle")
    h.check("cd, then regenerate the checksum", "shell",
            s("cd tests/golden && shasum -a 256 README.md > expected/SHA256SUMS"), "deny", "oracle")
    h.check("shell already inside tests/golden", "shell",
            s("echo x > expected/x", cwd=h.at("tests/golden")), "deny", "oracle")
    h.check("cat the answer key", "shell", s("cat tests/golden/expected/outcomes.toml"), "allow")
    h.check("copy the answer key out to /tmp", "shell",
            s("cat tests/golden/expected/outcomes.toml > /tmp/copy.toml"), "allow")
    h.check("grep -i the answer key", "shell",
            s("grep -i foo tests/golden/expected/outcomes.toml"), "allow")
    h.check("check the checksums", "shell",
            s("cd tests/golden && shasum -a 256 -c expected/SHA256SUMS"), "allow")
    h.check("cd into the oracle and list it", "shell", s("cd tests/golden/expected && ls"), "allow")
    h.check("rm in the oracle", "shell", s("rm tests/golden/expected/x"), "deny", "oracle")
    h.check("python writing the oracle", "shell",
            s("python -c \"open('tests/golden/expected/x','w')\""), "deny", "oracle")
    h.check("python writing the oracle, odd case", "shell",
            s("python -c \"open('tests/Golden/Expected/x','w')\""), "deny", "oracle")
    h.check("append to the controls log", "shell",
            s("echo fake >> .git/cursor-guard/controls.log"), "deny", "git-internals")
    h.check("touch the controls log", "shell",
            s("touch .git/cursor-guard/controls.log"), "deny", "git-internals")
    h.check("read the controls log", "shell", s("cat .git/cursor-guard/controls.log"), "allow")
    h.check("ls .cursor", "shell", s("ls -la .cursor"), "allow")
    h.check("sed -i on hooks.json", "shell",
            s("sed -i '' 's/a/b/' .cursor/hooks.json"), "deny", "kit")
    h.check("append to AGENTS.md", "shell", s("echo x >> AGENTS.md"), "deny", "kit")
    h.check("cat .env", "shell", s("cat .env"), "deny", "secrets")
    h.check("--env-file=.env", "shell", s("python app.py --env-file=.env"), "deny", "secrets")
    h.check("rm -rf .venv", "shell", s("rm -rf .venv"), "allow")
    h.check("rm -rf .git", "shell", s("rm -rf .git"), "deny", "git-internals")


def install_cases(h: Harness) -> None:
    print("== shell hook: tools and installs")
    s = h.shell
    h.check("pip install, editable", "shell",
            s('python -m pip install -c constraints.txt -e ".[dev]"'), "ask", "install")
    h.check("uv", "shell", s("uv pip install x"), "deny", "plain-python")
    h.check("pip freeze into constraints", "shell",
            s("pip freeze --exclude-editable > constraints.txt"), "allow")
    h.check("curl", "shell", s("curl https://example.com"), "ask", "network")
    h.check("python -m pytest a test named test_env.py", "shell",
            s("python -m pytest tests/unit/test_env.py -q"), "allow")
    h.check("payload with cmd instead of command", "shell", {"cmd": "git push"}, "deny", "push")
    h.check("payload with no command fails closed", "shell", {"something": "else"}, "deny", "guard")
    h.check("invalid JSON fails closed", "shell", None, "deny", raw="{not json", code=2)


def commit_cases(h: Harness) -> None:
    print("== commits: human-owned files and verify.py --fast")
    s = h.shell
    oracle = h.sandbox / "tests" / "golden" / "expected"
    oracle.mkdir(parents=True)
    (oracle / "outcomes.toml").write_text("x = 1\n")
    h.check("agent commit while the answer key is uncommitted", "shell", s(TRAILED), "deny",
            "human-files")
    h.git("add", "tests/golden/expected")
    h.git("commit", "-q", "-m", "Add the answer key")
    h.check("agent commit once the user has committed it", "shell", s(TRAILED), "allow")
    verify = h.sandbox / "scripts" / "verify.py"
    verify.parent.mkdir()
    verify.write_text("import sys\nsys.exit(0 if sys.argv[1:] == ['--fast'] else 3)\n")
    h.check("fast checks pass", "shell", s(TRAILED), "allow")
    verify.write_text("import sys\nprint('FAIL black'); sys.exit(1)\n")
    h.check("fast checks fail", "shell", s(TRAILED), "deny", "checks")
    (h.sandbox / "msg.txt").write_text("Add x\n\nAssisted-by: Cursor (Claude Opus 5.5)\n")
    verify.write_text("import sys\nsys.exit(0)\n")
    h.check("commit -F file with trailer", "shell", s("git commit -F msg.txt"), "allow")
    (h.sandbox / "bare.txt").write_text("Add x\n")
    h.check("commit -F file without trailer", "shell",
            s("git commit -F bare.txt"), "deny", "trailer")
    print("== unknown mode")
    h.check("unknown hook mode", "nonsense", {}, "deny", "guard")


def report(h: Harness, ok: bool, message: str) -> None:
    h.cases += 1
    if not ok:
        h.failures += 1
    print("{:4} {}".format("ok" if ok else "FAIL", message))


def state_checks(h: Harness) -> None:
    print("== the guard's own files stay inside .git/")
    state = h.sandbox / ".git" / "cursor-guard"
    log = state / "controls.log"
    lines = log.read_text().splitlines() if log.exists() else []
    count = len(lines)
    report(h, count > 0, "controls.log is in .git/cursor-guard/, with {} lines".format(count))
    leaked = [line for line in lines if h.root in line or str(Path.home()) in line]
    report(h, not leaked, "controls.log has no local paths")
    hooks_files = sorted(p.name for p in (h.sandbox / ".cursor" / "hooks").iterdir())
    clean = not (h.sandbox / "evidence").exists() and hooks_files == ["guard.py"]
    report(h, clean, "nothing written into the working tree (hooks folder: {})".format(hooks_files))
    (state / "DEBUG").write_text("")
    h.run("tool", tool("Read", path=h.at("README.md")))
    saved = list((state / "payloads").glob("*.json")) if (state / "payloads").exists() else []
    report(h, len(saved) > 0, "with .git/cursor-guard/DEBUG present, events are saved beside it")


def main() -> int:
    guard = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else default_guard()
    python = sys.argv[2] if len(sys.argv) > 2 else sys.executable
    h = Harness(guard, python)
    try:
        for cases in (tool_cases, read_cases, git_cases, wrapper_cases, path_cases, install_cases,
                      commit_cases, state_checks):
            cases(h)
    finally:
        shutil.rmtree(str(h.sandbox), ignore_errors=True)
    print("CASES: {}  FAILURES: {}".format(h.cases, h.failures))
    return 1 if h.failures else 0


if __name__ == "__main__":
    sys.exit(main())
