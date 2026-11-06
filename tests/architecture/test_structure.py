import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKED_ROOTS = (ROOT / "src", ROOT / "tests")
MAIN_SHIM = ["from mule_triage.cli import main", "raise SystemExit(main())"]


def python_files(root: Path) -> list[Path]:
    return [
        path for path in sorted(root.rglob("*.py")) if "__pycache__" not in path.parts
    ]


def folders_without_init(root: Path) -> list[str]:
    folders = sorted({path.parent for path in python_files(root)})
    return [
        f"{folder.relative_to(root.parent)}: no __init__.py"
        for folder in folders
        if not (folder / "__init__.py").is_file()
    ]


def inits_with_content(root: Path) -> list[str]:
    return [
        f"{path.relative_to(root.parent)}: isn't empty"
        for path in python_files(root)
        if path.name == "__init__.py" and path.read_text(encoding="utf-8").strip()
    ]


def main_shim_problems(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    if lines != MAIN_SHIM:
        return [f"{path.name}: isn't the two-line shim calling cli.main()"]
    return []


def non_stdlib_imports(root: Path) -> list[str]:
    problems: list[str] = []
    for path in python_files(root):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            problems.extend(
                f"{path.relative_to(root.parent)}:{node.lineno}: imports {name}"
                for name in names
                if name.split(".")[0] not in sys.stdlib_module_names
            )
    return problems


def relative_imports(root: Path) -> list[str]:
    return [
        f"{path.relative_to(root.parent)}:{node.lineno}: relative import"
        for path in python_files(root)
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.level > 0
    ]


def test_every_python_folder_has_an_init() -> None:
    problems = [
        problem for root in CHECKED_ROOTS for problem in folders_without_init(root)
    ]

    assert problems == []


def test_flags_a_python_folder_without_an_init(tmp_path: Path) -> None:
    (tmp_path / "package").mkdir()
    (tmp_path / "package" / "module.py").write_text("", encoding="utf-8")

    problems = folders_without_init(tmp_path)

    assert len(problems) == 1


def test_every_init_is_empty() -> None:
    problems = [
        problem for root in CHECKED_ROOTS for problem in inits_with_content(root)
    ]

    assert problems == []


def test_flags_an_init_that_isnt_empty(tmp_path: Path) -> None:
    (tmp_path / "__init__.py").write_text("import os\n", encoding="utf-8")

    problems = inits_with_content(tmp_path)

    assert len(problems) == 1


def test_main_is_a_two_line_shim_calling_cli_main() -> None:
    problems = main_shim_problems(ROOT / "src" / "mule_triage" / "__main__.py")

    assert problems == []


def test_flags_a_main_that_does_more_than_call_cli_main(tmp_path: Path) -> None:
    shim = tmp_path / "__main__.py"
    shim.write_text(
        "import sys\nfrom mule_triage.cli import main\n\nraise SystemExit(main())\n",
        encoding="utf-8",
    )

    problems = main_shim_problems(shim)

    assert len(problems) == 1


def test_flags_a_main_that_drops_the_exit_code(tmp_path: Path) -> None:
    shim = tmp_path / "__main__.py"
    shim.write_text("from mule_triage.cli import main\n\nmain()\n", encoding="utf-8")

    problems = main_shim_problems(shim)

    assert len(problems) == 1


def test_scripts_import_only_the_standard_library() -> None:
    problems = non_stdlib_imports(ROOT / "scripts")

    assert problems == []


def test_flags_a_script_that_imports_a_third_party_package(tmp_path: Path) -> None:
    (tmp_path / "tool.py").write_text(
        "import os\nfrom pydantic import BaseModel\n", encoding="utf-8"
    )

    problems = non_stdlib_imports(tmp_path)

    assert len(problems) == 1


def test_imports_are_absolute() -> None:
    problems = [problem for root in CHECKED_ROOTS for problem in relative_imports(root)]

    assert problems == []


def test_flags_a_relative_import(tmp_path: Path) -> None:
    (tmp_path / "module.py").write_text("from . import sibling\n", encoding="utf-8")

    problems = relative_imports(tmp_path)

    assert len(problems) == 1
