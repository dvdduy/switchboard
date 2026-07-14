"""Architecture checks for framework-isolated orchestration."""

import ast
from pathlib import Path

SOURCE_ROOT = Path("src/switchboard")
LANGGRAPH_ADAPTER_ROOT = SOURCE_ROOT / "adapters" / "orchestration"


def _imports_langgraph(path: Path) -> list[int]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    lines: list[int] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules = [node.module]
        else:
            continue

        if any(module == "langgraph" or module.startswith("langgraph.") for module in modules):
            lines.append(node.lineno)

    return lines


def test_only_orchestration_adapter_may_import_langgraph() -> None:
    violations = [
        f"{path}:{line} imports LangGraph outside the orchestration adapter"
        for path in SOURCE_ROOT.rglob("*.py")
        if not path.is_relative_to(LANGGRAPH_ADAPTER_ROOT)
        for line in _imports_langgraph(path)
    ]

    assert violations == [], "\n".join(violations)
