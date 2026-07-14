"""Architecture checks for public API route modules."""

import ast
from pathlib import Path

ROUTE_PATH = Path("src/switchboard/adapters/api/conversations.py")


def test_conversation_routes_do_not_query_persistence_or_launch_durable_work() -> None:
    source = ROUTE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(ROUTE_PATH))
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    } | {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert not any(
        module == prefix or module.startswith(f"{prefix}.")
        for module in imports
        for prefix in ("sqlalchemy", "switchboard.adapters.persistence")
    )
    assert "create_task" not in source
