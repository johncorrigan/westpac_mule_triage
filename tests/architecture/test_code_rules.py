import ast
import re
from collections.abc import Callable

import pytest

COMPOSITION_ROOT = "mule_triage.cli"
WALL_CLOCK = {
    ("datetime", "now"),
    ("datetime", "utcnow"),
    ("datetime", "today"),
    ("date", "today"),
    ("time", "time"),
    ("time", "time_ns"),
}
WALL_CLOCK_FROM_TIME = {"time", "time_ns"}
SQL_KEYWORDS = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b")
SHELL_FUNCTIONS = {("os", "system"), ("os", "popen")}
BROAD_EXCEPTIONS = {"Exception", "BaseException"}
MUTABLE_DEFAULTS = (
    ast.List,
    ast.Dict,
    ast.Set,
    ast.ListComp,
    ast.DictComp,
    ast.SetComp,
)
MUTABLE_FACTORIES = {"list", "dict", "set", "defaultdict", "bytearray"}
SQL_METHODS = {"execute", "executemany", "executescript"}
FORBIDDEN_CALLS = {"eval", "exec"}

Rule = Callable[[str, ast.Module], list[str]]


def last_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def calls(tree: ast.Module) -> list[ast.Call]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


def import_aliases(tree: ast.Module) -> dict[str, str]:
    return {
        alias.asname: alias.name.split(".")[-1]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
        if alias.asname
    }


def wall_clock_calls(module: str, tree: ast.Module) -> list[str]:
    if module == COMPOSITION_ROOT:
        return []
    aliases = import_aliases(tree)
    found = [
        f"{module}:{node.lineno}: uses the wall clock"
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and (aliases.get(last_name(node.value), last_name(node.value)), node.attr)
        in WALL_CLOCK
    ]
    found.extend(
        f"{module}:{node.lineno}: imports the wall clock from time"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "time"
        and any(alias.name in WALL_CLOCK_FROM_TIME for alias in node.names)
    )
    return found


def broad_excepts(module: str, tree: ast.Module) -> list[str]:
    if module == COMPOSITION_ROOT:
        return []
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        caught = node.type
        items = caught.elts if isinstance(caught, ast.Tuple) else [caught]
        names = {last_name(item) for item in items if item is not None}
        if caught is None or BROAD_EXCEPTIONS.intersection(names):
            found.append(f"{module}:{node.lineno}: bare or broad except")
    return found


def is_mutable(default: ast.expr | None) -> bool:
    if isinstance(default, MUTABLE_DEFAULTS):
        return True
    return (
        isinstance(default, ast.Call) and last_name(default.func) in MUTABLE_FACTORIES
    )


def mutable_defaults(module: str, tree: ast.Module) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            defaults = [*node.args.defaults, *node.args.kw_defaults]
            if any(is_mutable(default) for default in defaults):
                found.append(f"{module}:{node.lineno}: mutable default argument")
    return found


def is_built_text(node: ast.AST) -> bool:
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add | ast.Mod):
        return True
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "format"
    )


def literal_text(node: ast.AST) -> str:
    return "".join(
        child.value
        for child in ast.walk(node)
        if isinstance(child, ast.Constant) and isinstance(child.value, str)
    )


def built_sql(module: str, tree: ast.Module) -> list[str]:
    lines = {
        call.lineno
        for call in calls(tree)
        if isinstance(call.func, ast.Attribute)
        and call.func.attr in SQL_METHODS
        and call.args
        and is_built_text(call.args[0])
    }
    lines.update(
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.expr)
        and is_built_text(node)
        and SQL_KEYWORDS.search(literal_text(node))
    )
    return [f"{module}:{line}: SQL built from text" for line in sorted(lines)]


def prints(module: str, tree: ast.Module) -> list[str]:
    if module == COMPOSITION_ROOT:
        return []
    return [
        f"{module}:{call.lineno}: print() outside cli.py"
        for call in calls(tree)
        if isinstance(call.func, ast.Name) and call.func.id == "print"
    ]


def unsafe_calls(module: str, tree: ast.Module) -> list[str]:
    found = [
        f"{module}:{call.lineno}: {call.func.id}()"
        for call in calls(tree)
        if isinstance(call.func, ast.Name) and call.func.id in FORBIDDEN_CALLS
    ]
    found.extend(
        f"{module}:{call.lineno}: shell=True"
        for call in calls(tree)
        for keyword in call.keywords
        if keyword.arg == "shell"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
    )
    found.extend(
        f"{module}:{call.lineno}: {last_name(call.func)}() runs a shell"
        for call in calls(tree)
        if isinstance(call.func, ast.Attribute)
        and (last_name(call.func.value), call.func.attr) in SHELL_FUNCTIONS
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        else:
            continue
        if any(name.split(".")[0] == "pickle" for name in modules):
            found.append(f"{module}:{node.lineno}: imports pickle")
    return found


RULES: dict[str, Rule] = {
    "wall clock": wall_clock_calls,
    "broad except": broad_excepts,
    "mutable default": mutable_defaults,
    "built SQL": built_sql,
    "print": prints,
    "unsafe call": unsafe_calls,
}


def test_the_source_breaks_no_code_rule(source_trees: dict[str, ast.Module]) -> None:
    violations = [
        violation
        for module, tree in source_trees.items()
        for rule in RULES.values()
        for violation in rule(module, tree)
    ]

    assert violations == []


@pytest.mark.parametrize(
    ("rule", "snippet"),
    [
        ("wall clock", "datetime.now()"),
        ("wall clock", "datetime.datetime.utcnow()"),
        ("wall clock", "date.today()"),
        ("wall clock", "time.time()"),
        ("wall clock", "from time import time"),
        ("wall clock", "from datetime import datetime as dt\ndt.now()"),
        ("wall clock", "import time as clock\nclock.time()"),
        ("wall clock", "time.time_ns()"),
        ("wall clock", "stamp = field(default_factory=datetime.now)"),
        ("broad except", "try:\n    pass\nexcept:\n    pass"),
        ("broad except", "try:\n    pass\nexcept Exception:\n    pass"),
        (
            "broad except",
            "try:\n    pass\nexcept (ValueError, BaseException):\n    pass",
        ),
        ("mutable default", "def f(items=[]):\n    pass"),
        ("mutable default", "def f(*, seen=set()):\n    pass"),
        ("built SQL", 'conn.execute(f"SELECT * FROM t WHERE id = {x}")'),
        ("built SQL", 'conn.execute("SELECT * FROM t WHERE id = " + x)'),
        ("built SQL", 'conn.execute("SELECT * FROM t WHERE id = {}".format(x))'),
        ("built SQL", 'sql = f"SELECT * FROM t WHERE id = {x}"\nconn.execute(sql)'),
        ("print", 'print("hello")'),
        ("unsafe call", 'eval("1")'),
        ("unsafe call", 'exec("x = 1")'),
        ("unsafe call", "import pickle"),
        ("unsafe call", 'subprocess.run("ls", shell=True)'),
        ("unsafe call", 'os.system("ls")'),
        ("unsafe call", 'os.popen("ls")'),
    ],
    ids=[
        "datetime.now",
        "datetime.utcnow",
        "date.today",
        "time.time",
        "from time import time",
        "aliased datetime",
        "aliased time module",
        "time.time_ns",
        "uncalled datetime.now",
        "bare except",
        "except Exception",
        "except BaseException in a tuple",
        "list default",
        "set() keyword default",
        "f-string SQL",
        "concatenated SQL",
        "formatted SQL",
        "f-string SQL in a variable",
        "print",
        "eval",
        "exec",
        "pickle",
        "shell=True",
        "os.system",
        "os.popen",
    ],
)
def test_each_code_rule_flags_its_bad_snippet(rule: str, snippet: str) -> None:
    tree = ast.parse(snippet)

    violations = RULES[rule]("mule_triage.store", tree)

    assert len(violations) == 1


@pytest.mark.parametrize(
    ("rule", "snippet"),
    [
        ("wall clock", "time.perf_counter()"),
        ("broad except", "try:\n    pass\nexcept ValueError:\n    pass"),
        ("mutable default", "def f(items=()):\n    pass"),
        ("built SQL", 'conn.execute("SELECT * FROM t WHERE id = ?", (x,))'),
        ("built SQL", 'sql = "SELECT * FROM t WHERE id = ?"\nconn.execute(sql, (x,))'),
        ("built SQL", 'message = f"Updated {count} accounts"'),
    ],
    ids=[
        "perf_counter",
        "specific except",
        "tuple default",
        "placeholder SQL",
        "placeholder SQL in a variable",
        "f-string without SQL",
    ],
)
def test_each_code_rule_allows_the_safe_form(rule: str, snippet: str) -> None:
    tree = ast.parse(snippet)

    violations = RULES[rule]("mule_triage.store", tree)

    assert violations == []


@pytest.mark.parametrize(
    ("rule", "snippet"),
    [
        ("wall clock", "datetime.now()"),
        ("broad except", "try:\n    pass\nexcept Exception:\n    pass"),
        ("print", 'print("hello")'),
    ],
    ids=["wall clock", "broad except", "print"],
)
def test_cli_may_read_the_clock_catch_broadly_and_print(
    rule: str, snippet: str
) -> None:
    tree = ast.parse(snippet)

    violations = RULES[rule]("mule_triage.cli", tree)

    assert violations == []
