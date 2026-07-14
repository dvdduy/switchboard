"""Tests enforcing dependency direction around the application layer."""

import ast
from pathlib import Path

APPLICATION_ROOT = Path("src/switchboard/application")

FORBIDDEN_IMPORT_PREFIXES = (
    "alembic",
    "fastapi",
    "langchain",
    "langgraph",
    "psycopg",
    "redis",
    "sqlalchemy",
    "switchboard.adapters",
    "switchboard.bootstrap",
    "switchboard.workers",
)


def _is_forbidden(module_name: str) -> bool:
    return any(
        module_name == prefix or module_name.startswith(f"{prefix}.")
        for prefix in FORBIDDEN_IMPORT_PREFIXES
    )


def _find_forbidden_imports(path: Path) -> list[str]:
    tree = ast.parse(
        path.read_text(encoding="utf-8"),
        filename=str(path),
    )
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules = [node.module]
        else:
            continue

        for module_name in imported_modules:
            if _is_forbidden(module_name):
                violations.append(f"{path}:{node.lineno} imports forbidden module {module_name!r}")

    return violations


def test_application_does_not_depend_on_adapters_or_frameworks() -> None:
    violations = [
        violation
        for path in APPLICATION_ROOT.rglob("*.py")
        for violation in _find_forbidden_imports(path)
    ]

    assert violations == [], (
        "The application layer may depend on domain and standard "
        "Python abstractions, but not adapters or infrastructure:\n" + "\n".join(violations)
    )
