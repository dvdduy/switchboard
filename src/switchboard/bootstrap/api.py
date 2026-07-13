"""Switchboard API composition root."""

from fastapi import FastAPI

from switchboard.adapters.api.app import create_app
from switchboard.bootstrap.config import load_settings
from switchboard.bootstrap.resources import build_runtime_resources


def build_app() -> FastAPI:
    """Construct the API and all of its runtime dependencies."""

    settings = load_settings()
    resources = build_runtime_resources(settings)

    return create_app(
        settings=settings,
        readiness_service=resources.readiness_service,
        close_resources=resources.close,
    )


app = build_app()
