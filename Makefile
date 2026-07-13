.PHONY: format format-check lint type test architecture check docker-build

format:
	uv run ruff format .
	uv run ruff check --fix .

format-check:
	uv run ruff format --check .

lint:
	uv run ruff check .

type:
	uv run mypy

test:
	uv run pytest

architecture:
	uv run pytest tests/architecture -q

check:
	uv lock --check
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	uv run pytest

docker-build:
	docker build --tag switchboard:local .