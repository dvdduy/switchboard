.PHONY: \
	format \
	format-check \
	lint \
	type \
	test \
	integration \
	architecture \
	check \
	docker-build \
	test-db-up \
	test-db-down \
	migration-up \
	migration-down \
	migration-current

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

migration-up:
	uv run alembic upgrade head

migration-down:
	uv run alembic downgrade -1

migration-current:
	uv run alembic current

test-db-up:
	docker compose --profile test up -d postgres-test

test-db-down:
	docker compose --profile test stop postgres-test
	docker compose --profile test rm -f postgres-test

integration:
	uv run pytest tests/integration -q