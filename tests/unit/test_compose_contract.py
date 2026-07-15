from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_runtime_image_contains_migrations_and_compose_orders_startup() -> None:
    dockerfile = (PROJECT_ROOT / "Dockerfile").read_text(encoding="utf-8")
    compose = (PROJECT_ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "COPY alembic.ini ./" in dockerfile
    assert "COPY migrations ./migrations" in dockerfile
    assert "  migrate:" in compose
    assert "      - alembic\n      - upgrade\n      - head" in compose
    assert compose.count("condition: service_completed_successfully") == 2
