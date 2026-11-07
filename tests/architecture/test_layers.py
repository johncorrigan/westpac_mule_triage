import ast

import pytest

PACKAGE = "mule_triage"
LAYERS = {
    "contracts": 1,
    "errors": 1,
    "clock": 1,
    "records": 2,
    "detection": 3,
    "evidence": 4,
    "narrative": 4,
    "decision": 5,
    "report": 6,
    "generate": 6,
    "pipeline": 6,
    "cli": 6,
}
STRUCTURAL = {"__init__", "__main__"}


def imported_parts(node: ast.Import | ast.ImportFrom) -> list[str]:
    if isinstance(node, ast.Import):
        return [
            alias.name.split(".")[1]
            for alias in node.names
            if alias.name.startswith(PACKAGE + ".")
        ]
    if node.level > 0 or node.module is None:
        return []
    if node.module == PACKAGE:
        return [alias.name for alias in node.names]
    if node.module.startswith(PACKAGE + "."):
        return [node.module.split(".")[1]]
    return []


def layer_violations(module: str, tree: ast.Module) -> list[str]:
    part = module.split(".")[1]
    if part in STRUCTURAL:
        return []
    if part not in LAYERS:
        return [f"{module}: isn't in the layer map"]
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import | ast.ImportFrom):
            continue
        for target in imported_parts(node):
            target_layer = LAYERS.get(target)
            if target_layer is not None and target_layer > LAYERS[part]:
                violations.append(
                    f"{module}:{node.lineno}: layer {LAYERS[part]} imports "
                    f"{target}, which is in layer {target_layer}"
                )
    return violations


def test_reads_the_package_source(source_trees: dict[str, ast.Module]) -> None:
    modules = set(source_trees)

    assert {"mule_triage.cli", "mule_triage.__main__"} <= modules


def test_every_module_is_mapped_and_imports_only_its_layer_or_below(
    source_trees: dict[str, ast.Module],
) -> None:
    violations = [
        violation
        for module, tree in source_trees.items()
        for violation in layer_violations(module, tree)
    ]

    assert violations == []


@pytest.mark.parametrize(
    ("module", "snippet"),
    [
        ("mule_triage.records.store", "from mule_triage.cli import main"),
        ("mule_triage.contracts", "import mule_triage.detection.signals"),
        ("mule_triage.records.ingest", "from mule_triage import decision"),
        ("mule_triage.helpers", ""),
    ],
    ids=["from-import above", "import above", "package import above", "unmapped"],
)
def test_flags_an_import_from_a_higher_layer_or_an_unmapped_module(
    module: str, snippet: str
) -> None:
    tree = ast.parse(snippet)

    violations = layer_violations(module, tree)

    assert len(violations) == 1


def test_allows_an_import_from_the_same_layer_or_below() -> None:
    tree = ast.parse(
        "from mule_triage.records.store import save\nfrom mule_triage.contracts import Payment"
    )

    violations = layer_violations("mule_triage.records.ingest", tree)

    assert violations == []
