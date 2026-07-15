# syntax=docker/dockerfile:1

FROM python:3.13-slim

ARG UV_VERSION=0.11.28

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN pip install --no-cache-dir "uv==${UV_VERSION}"

WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations

RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:${PATH}"

RUN addgroup --system switchboard \
    && adduser --system --ingroup switchboard switchboard \
    && chown -R switchboard:switchboard /app

USER switchboard

EXPOSE 8000

CMD ["uvicorn", "switchboard.bootstrap.api:app", "--host", "0.0.0.0", "--port", "8000"]
