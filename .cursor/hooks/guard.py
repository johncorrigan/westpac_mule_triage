#!/usr/bin/env python3
"""Project guard for Cursor hooks: blocks what AGENTS.md forbids, where a script can tell.

Run by .cursor/hooks.json as `python3 .cursor/hooks/guard.py shell|tool|read`. It reads the hook
event as JSON on stdin and prints its decision as JSON on stdout. Every block or approval request
is appended to .git/cursor-guard/controls.log, with local paths removed. Any error in the guard
blocks the action.

It reads text, so it can't see through variables, wildcards, Git aliases from the user's own
configuration, or code that builds a path. The golden checksum, the user's own commits of
human-owned files, and diff review cover those routes.

Standard library only, and compatible with Python 3.8, because Cursor may run the system python3
rather than the project's virtual environment.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

HOOKS_DIR = Path(os.path.realpath(__file__)).parent
ROOT = HOOKS_DIR.parent.parent
TRAILER = "Assisted-by:"


def find_git_dir(root: Path) -> Path | None:
    """The repository's Git folder, following the pointer file a worktree or submodule uses."""
    dot_git = root / ".git"
    if dot_git.is_dir():
        return dot_git
    if dot_git.is_file():
        text = dot_git.read_text(encoding="utf-8").strip()
        if text.startswith("gitdir:"):
            return Path(os.path.realpath(str(root / text[len("gitdir:") :].strip())))
    return None


# The guard's own files live inside the Git folder, which Git never tracks, so they need no
# .gitignore entry, and the agent can't change them because the guard protects .git/.
GIT_DIR = find_git_dir(ROOT)
STATE = GIT_DIR / "cursor-guard" if GIT_DIR else Path.home() / ".cursor-guard" / ROOT.name
LOG = STATE / "controls.log"
DEBUG_FLAG = STATE / "DEBUG"
PAYLOADS = STATE / "payloads"

KIT_REASON = "AGENTS.md and .cursor/ are the user's; propose the change."
# Paths the agent may read but never change: (path in lower case, rule, reason). Adjust per project.
PROTECTED = (
    ("tests/golden/expected", "oracle", "tests/golden/expected/ is the user's answer key."),
    ("tests/ai/recorded", "recordings", "Recorded model replies are the user's; re-record them."),
    ("agents.md", "kit", KIT_REASON),
    (".cursorignore", "kit", KIT_REASON),
    (".cursor", "kit", KIT_REASON),
    (".git", "git-internals", "Git's own files are off limits."),
)
# Human-owned folders, which the user commits by hand before any agent commit.
HUMAN_COMMITTED = ("tests/golden/expected", "tests/ai/recorded")

ENV_FILE = re.compile(r"(^|/)\.env(\.[^/]+)?$", re.IGNORECASE)
ENV_TEMPLATE = re.compile(r"(^|/)\.env\.(example|sample|template)$", re.IGNORECASE)
WRITE_TOOL = re.compile(
    r"write|edit|replace|delete|remove|create|move|rename|notebook|apply|patch", re.IGNORECASE
)
READ_TOOL = re.compile(r"read|glob|grep|search|list|fetch|lint|^ls$", re.IGNORECASE)
SHELL_TOOL = re.compile(r"shell|terminal|command", re.IGNORECASE)
PATH_KEY = re.compile(r"path|file|target|notebook|dir", re.IGNORECASE)
CONTENT_KEY = re.compile(r"content|text|string|body|code", re.IGNORECASE)
ENV_ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
IDENTITY_VARIABLE = re.compile(r"^GIT_(AUTHOR|COMMITTER)_(NAME|EMAIL|DATE)=", re.IGNORECASE)
PIP_INSTALL = re.compile(r"(^|[\s/])pip3?(\.exe)?\s+install\b|-m\s+pip\s+install\b")
WRITE_MARKERS = re.compile(r"\btee\b|--in-place|\s-delete\b|\s-exec\b|\bof=")
REDIRECT_TARGET = re.compile(r">>?\|?\s*([^\s;&|<>]+)")
SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")
HEREDOC = re.compile(r"^\s*cat\s+<<")

READ_ONLY_COMMANDS = {
    "cat", "ls", "head", "tail", "wc", "grep", "rg", "diff", "shasum", "sha256sum", "file",
    "stat", "less", "more", "find", "tree", "echo", "printf", "pwd", "test", "[",
}
READ_ONLY_GIT = {"diff", "log", "status", "show", "ls-files", "blame", "add"}
GIT_FILE_WRITERS = {"checkout", "restore", "rm", "mv", "apply", "am", "switch"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "fish"}
STRIP_WRAPPERS = {"env", "command", "builtin", "nohup", "time", "exec", "nice", "xargs",
                  "timeout", "caffeinate", "stdbuf"}
FOREIGN_TOOLS = {"uv", "uvx", "ruff", "poetry", "conda", "mamba", "pipx", "pdm", "hatch", "rye"}
HISTORY_GIT = {"rebase", "reset", "filter-branch", "filter-repo", "replace", "update-ref"}
INLINE_CONFIG = ("alias.", "user.", "core.hookspath", "core.sshcommand")
LISTING_BRANCH_FLAGS = {"-a", "-r", "-l", "-v", "-vv", "--all", "--remotes", "--list"}
READING_CONFIG_FLAGS = {"--get", "--get-all", "--get-regexp", "--list", "-l", "--show-origin"}
NETWORK_COMMANDS = {"curl", "wget", "npm", "npx", "yarn", "pnpm"}


def now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def path_forms(path: str) -> list:
    return sorted({path, os.path.realpath(path)}, key=len, reverse=True)


ROOT_FORMS = path_forms(str(ROOT))
HOME_FORMS = path_forms(os.path.expanduser("~"))


def redact(text: str) -> str:
    """Remove the repository's location and the home folder, so the log can be published."""
    for form in ROOT_FORMS:
        text = text.replace(form + "/", "").replace(form, ".")
    for form in HOME_FORMS:
        text = text.replace(form, "~")
    return text


def log(hook: str, decision: str, rule: str, detail: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "time": now_utc(),
        "hook": hook,
        "decision": decision,
        "rule": rule,
        "detail": redact(detail)[:300],
    }
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def save_payload(hook: str, payload: object, always: bool = False) -> None:
    if not (always or DEBUG_FLAG.exists()):
        return
    PAYLOADS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    target = PAYLOADS / "{}-{}.json".format(hook, stamp)
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def allow() -> dict:
    return {"permission": "allow"}


def block(hook: str, rule: str, detail: str, reason: str) -> dict:
    log(hook, "deny", rule, detail)
    return {
        "permission": "deny",
        "user_message": "Blocked by the project guard ({}): {}".format(rule, reason),
        "agent_message": "Blocked ({}): {} Stop and tell the user; don't work around it.".format(
            rule, reason
        ),
    }


def ask(hook: str, rule: str, detail: str, reason: str) -> dict:
    log(hook, "ask", rule, detail)
    return {
        "permission": "ask",
        "user_message": "Approval needed ({}): {}".format(rule, reason),
        "agent_message": "This needs the user's approval ({}): {}".format(rule, reason),
    }


def relative(path_text: str, base: str = "") -> str | None:
    """The path relative to the repository root, or None when it's outside the repository."""
    text = path_text.strip().strip("'\"").replace("\\", "/")
    if not text:
        return None
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = Path(base or str(ROOT)) / candidate
    try:
        return Path(os.path.realpath(str(candidate))).relative_to(ROOT).as_posix()
    except ValueError:
        return None


def is_env_file(path_text: str) -> bool:
    normal = path_text.strip().strip("'\"").replace("\\", "/")
    return bool(ENV_FILE.search(normal)) and not ENV_TEMPLATE.search(normal)


def protected_rule(rel: str) -> tuple | None:
    """The rule and reason protecting a repository path, ignoring case as macOS does."""
    lowered = rel.lower()
    for prefix, rule, reason in PROTECTED:
        if lowered == prefix or lowered.startswith(prefix + "/"):
            return rule, reason
    if is_env_file(lowered):
        return "secrets", ".env files may hold secrets."
    return None


def path_values(value: object, key: str = "") -> list:
    """Every string stored under a key that names a path, at any depth of the payload."""
    found = []
    if isinstance(value, dict):
        for child_key, child in value.items():
            found.extend(path_values(child, str(child_key)))
    elif isinstance(value, list):
        for item in value:
            found.extend(path_values(item, key))
    elif isinstance(value, str) and PATH_KEY.search(key) and not CONTENT_KEY.search(key):
        found.append(value)
    return found


def tool_name(payload: dict) -> str:
    for key in ("tool_name", "toolName", "tool", "name"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""


def decide_tool(payload: dict) -> dict:
    name = tool_name(payload)
    writes = bool(WRITE_TOOL.search(name))
    if SHELL_TOOL.search(name) and not writes:
        return allow()
    paths = path_values(payload)
    if READ_TOOL.search(name) and not writes:
        for text in paths:
            if is_env_file(text):
                return block("tool", "secrets", text, ".env files may hold secrets.")
        return allow()
    for text in paths:
        rel = relative(text)
        found = protected_rule(rel) if rel is not None else None
        if found is not None:
            return block("tool", found[0], rel or text, found[1])
    return allow()


def decide_read(payload: dict) -> dict:
    for text in path_values(payload):
        if is_env_file(text):
            log("read", "deny", "secrets", text)
            return {"permission": "deny"}
    return allow()


def split_segments(command: str) -> list:
    """Split a command line on ; && || | and newlines that aren't inside quotes."""
    segments = []
    current = []
    quote = ""
    index = 0
    while index < len(command):
        char = command[index]
        if quote:
            if char == "\\" and quote == '"' and index + 1 < len(command):
                current.append(command[index : index + 2])
                index += 2
                continue
            if char == quote:
                quote = ""
            current.append(char)
            index += 1
            continue
        if char in ("'", '"'):
            quote = char
            current.append(char)
            index += 1
            continue
        if command.startswith("&&", index) or command.startswith("||", index):
            segments.append("".join(current))
            current = []
            index += 2
            continue
        if char in ";|\n":
            segments.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
    segments.append("".join(current))
    return [segment.strip() for segment in segments if segment.strip()]


def words(segment: str) -> list:
    try:
        return shlex.split(segment)
    except ValueError:
        return segment.split()


def git_globals(tokens: list) -> tuple:
    """Git's global options as (option, value) pairs, and the arguments from the subcommand on."""
    rest = list(tokens[1:])
    options = []
    while rest and rest[0].startswith("-"):
        option = rest.pop(0)
        value = ""
        if option in ("-C", "-c", "--git-dir", "--work-tree", "--namespace") and rest:
            value = rest.pop(0)
        options.append((option, value))
    return options, rest


def unwrap(program: str, tokens: list) -> str | None:
    """The command a wrapper such as `bash -c` or `env` would run, or None if it isn't one."""
    rest = list(tokens[1:])
    if program in SHELLS:
        for index, token in enumerate(rest):
            if token.startswith("-") and "c" in token.lstrip("-") and index + 1 < len(rest):
                return rest[index + 1]
        return None
    if program == "eval":
        return " ".join(rest)
    if program not in STRIP_WRAPPERS:
        return None
    while rest and (rest[0].startswith("-") or ENV_ASSIGNMENT.match(rest[0])):
        option = rest.pop(0)
        if program == "env" and option in ("-u", "--unset") and rest:
            rest.pop(0)
    if program == "timeout" and rest:
        rest.pop(0)
    if not rest:
        return "" if program == "env" else None
    return " ".join(shlex.quote(part) for part in rest)


def writes_freely(tokens: list, program: str, segment: str) -> bool:
    """True when the command may write to any path it names, not only through redirection."""
    if program == "git":
        args = git_globals(tokens)[1]
        return bool(args) and args[0] in GIT_FILE_WRITERS
    return program not in READ_ONLY_COMMANDS or bool(WRITE_MARKERS.search(segment))


def write_targets(segment: str, tokens: list, program: str) -> list:
    targets = [match.group(1).strip("'\"") for match in REDIRECT_TARGET.finditer(segment)]
    if writes_freely(tokens, program, segment):
        if program == "git":
            args = git_globals(tokens)[1]
            targets.extend(arg for arg in args[1:] if not arg.startswith("-"))
        else:
            named = (token for token in tokens[1:] if not token.startswith("-"))
            targets.extend(token.split("=", 1)[-1] for token in named)
    return targets


def guarded_write(segment: str, tokens: list, program: str, cwd: str) -> dict | None:
    lowered = segment.lower()
    if writes_freely(tokens, program, segment):
        for prefix, rule, reason in PROTECTED:
            if prefix != ".git" and prefix in lowered:
                return block("shell", rule, segment, reason)
    for target in write_targets(segment, tokens, program):
        rel = relative(target, cwd)
        found = protected_rule(rel) if rel is not None else None
        if found is not None:
            return block("shell", found[0], segment, found[1])
    return None


def venv_python() -> str:
    for candidate in (ROOT / ".venv" / "bin" / "python", ROOT / ".venv" / "Scripts" / "python.exe"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def has_trailer(rest: list, command: str) -> bool:
    for position, arg in enumerate(rest):
        source = ""
        if arg in ("-F", "--file") and position + 1 < len(rest):
            source = rest[position + 1]
        elif arg.startswith("--file="):
            source = arg[len("--file=") :]
        if source and source != "-":
            try:
                return TRAILER in (ROOT / source).read_text(encoding="utf-8")
            except OSError:
                return False
    return TRAILER in command


def uncommitted(paths: tuple) -> list | None:
    """Changed or untracked files under the given paths, or None if Git can't say."""
    try:
        done = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all", "--", *paths],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    return [line[3:] for line in done.stdout.splitlines() if line.strip()]


def judge_commit(segment: str, rest: list, command: str) -> dict | None:
    if not has_trailer(rest, command):
        return block(
            "shell",
            "trailer",
            segment,
            "Agent commits carry the trailer 'Assisted-by: Cursor (<model>)' from AGENTS.md.",
        )
    pending = uncommitted(HUMAN_COMMITTED)
    if pending is None:
        return block("shell", "checks", segment, "git status failed, so the commit wasn't checked.")
    if pending:
        reason = "The user commits these by hand first: {}".format(", ".join(pending[:3]))
        return block("shell", "human-files", segment, reason)
    verify = ROOT / "scripts" / "verify.py"
    if not verify.exists():
        return None
    try:
        done = subprocess.run(
            [venv_python(), str(verify), "--fast"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=75,
        )
    except (OSError, subprocess.TimeoutExpired) as err:
        return block("shell", "checks", segment, "The fast checks couldn't run: {}".format(err))
    if done.returncode != 0:
        tail = "\n".join((done.stdout + done.stderr).strip().splitlines()[-15:])
        return block("shell", "checks", segment, "verify.py --fast failed:\n" + tail)
    return None


def judge_git(segment: str, tokens: list, command: str) -> dict | None:
    options, args = git_globals(tokens)
    if any(option == "-c" and value.lower().startswith(INLINE_CONFIG) for option, value in options):
        return block("shell", "git-config", segment, "No inline Git aliases or identity settings.")
    if not args:
        return None
    sub, rest = args[0], args[1:]
    if sub == "push":
        return block("shell", "push", segment, "Pushing is the user's job.")
    if sub in HISTORY_GIT or (sub == "commit" and "--amend" in rest):
        return block("shell", "history", segment, "No amending, rebasing, resetting or rewriting.")
    if sub == "commit":
        if any(arg.startswith(("--author", "--date")) for arg in rest):
            return block("shell", "identity", segment, "Commits keep the user's identity and time.")
        if "--no-verify" in rest or "-n" in rest:
            return block("shell", "checks", segment, "Commits don't skip the checks.")
        return judge_commit(segment, rest, command)
    if sub == "tag":
        return block("shell", "branches", segment, "No tags.")
    changes_branch = any(
        arg not in LISTING_BRANCH_FLAGS and not arg.startswith("--show") for arg in rest
    )
    if sub == "branch" and changes_branch:
        return block("shell", "branches", segment, "No branch changes; the work stays on main.")
    new_branch = any(arg in ("-b", "-B", "-c", "-C", "--orphan") for arg in rest)
    if sub in ("checkout", "switch") and new_branch:
        return block("shell", "branches", segment, "No new branches.")
    if sub == "config" and not any(arg in READING_CONFIG_FLAGS for arg in rest):
        return block("shell", "git-config", segment, "Git configuration is the user's.")
    if sub == "clean" and any(arg.startswith("-") and "f" in arg for arg in rest):
        return block("shell", "destructive", segment, "git clean deletes untracked work.")
    if sub == "stash" and rest and rest[0] in ("drop", "clear"):
        return block("shell", "destructive", segment, "Dropping a stash loses work.")
    if sub == "clone":
        return ask("shell", "network", segment, "Cloning fetches code from the network.")
    return None


def judge_segment(segment: str, command: str, state: dict, depth: int) -> dict | None:
    tokens = words(segment)
    if any(IDENTITY_VARIABLE.match(token) for token in tokens):
        return block("shell", "identity", segment, "Commits keep the user's identity and time.")
    while tokens and ENV_ASSIGNMENT.match(tokens[0]):
        tokens = tokens[1:]
    for found in SUBSTITUTION.findall(segment):
        inner = found[0] or found[1]
        if inner and not HEREDOC.match(inner):
            verdict = judge_command(inner, state["cwd"], depth + 1)
            if verdict is not None:
                return verdict
    if not tokens:
        return None
    program = os.path.basename(tokens[0]).lower()
    wrapped = unwrap(program, tokens)
    if wrapped == "":
        return block("shell", "secrets", segment, "Don't print environment variables.")
    if wrapped is not None:
        return judge_command(wrapped, state["cwd"], depth + 1)
    if program in ("cd", "pushd"):
        target = tokens[1] if len(tokens) > 1 else "~"
        state["cwd"] = os.path.realpath(os.path.join(state["cwd"], os.path.expanduser(target)))
        return None
    if program == "sudo":
        return block("shell", "sudo", segment, "No sudo.")
    if program in FOREIGN_TOOLS or (program == "brew" and "install" in tokens[1:2]):
        return block("shell", "plain-python", segment, "Plain Python and pip only.")
    if program == "printenv":
        return block("shell", "secrets", segment, "Don't print environment variables.")
    if any(is_env_file(token.split("=", 1)[-1]) for token in tokens[1:]):
        return block("shell", "secrets", segment, ".env files may hold secrets.")
    guarded = guarded_write(segment, tokens, program, state["cwd"])
    if guarded is not None:
        return guarded
    if program == "git":
        return judge_git(segment, tokens, command)
    if program == "gh":
        return ask("shell", "github", segment, "GitHub CLI commands need the user's approval.")
    if program == "rm" and any(arg in ("/", "~", ".", "..", "*") for arg in tokens[1:]):
        return block("shell", "destructive", segment, "That rm could delete far too much.")
    if PIP_INSTALL.search(segment):
        return ask("shell", "install", segment, "Installing packages needs the user's approval.")
    if program in NETWORK_COMMANDS:
        return ask("shell", "network", segment, "Network calls need the user's approval.")
    return None


def judge_command(command: str, cwd: str, depth: int = 0) -> dict | None:
    if depth > 3:
        return block("shell", "guard", command, "The command is nested too deeply to check.")
    state = {"cwd": cwd}
    for segment in split_segments(command):
        verdict = judge_segment(segment, command, state, depth)
        if verdict is not None:
            return verdict
    return None


def command_of(payload: dict) -> str | None:
    for container in (payload, payload.get("tool_input"), payload.get("input")):
        if isinstance(container, dict):
            for key in ("command", "cmd"):
                value = container.get(key)
                if isinstance(value, str):
                    return value
    return None


def decide_shell(payload: dict) -> dict:
    command = command_of(payload)
    if command is None:
        save_payload("shell", payload, always=True)
        return block(
            "shell",
            "guard",
            "no command in payload",
            "The guard couldn't find the command; see .git/cursor-guard/payloads/.",
        )
    cwd = payload.get("cwd")
    start = cwd if isinstance(cwd, str) and os.path.isabs(cwd) else str(ROOT)
    return judge_command(command, start) or allow()


def main() -> int:
    hook = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        raw = sys.stdin.read()
        payload = json.loads(raw) if raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        save_payload(hook, payload)
        if hook == "shell":
            decision = decide_shell(payload)
        elif hook == "tool":
            decision = decide_tool(payload)
        elif hook == "read":
            decision = decide_read(payload)
        else:
            decision = block(hook or "unknown", "guard", hook, "Unknown hook mode.")
    except Exception as err:  # the guard fails closed: any error blocks the action
        message = "Project guard error: {}".format(err)
        print(json.dumps({"permission": "deny", "user_message": message}))
        return 2
    print(json.dumps(decision))
    return 0


if __name__ == "__main__":
    sys.exit(main())
