"""Deterministic structured model gateway for local development and tests."""

from collections.abc import Iterable

from switchboard.application.errors import MalformedModelOutputError, ModelGatewayError
from switchboard.application.ports.agent_orchestrator import MAX_ORCHESTRATION_STEPS
from switchboard.application.ports.model_gateway import (
    ModelAction,
    ModelGateway,
    ModelRequest,
)


class ScriptedModelGateway(ModelGateway):
    """Return a fixed sequence of actions or safe gateway failures."""

    def __init__(self, steps: Iterable[ModelAction | ModelGatewayError]) -> None:
        self._steps = tuple(steps)
        if not self._steps:
            raise ValueError("scripted model gateway requires at least one step")
        if len(self._steps) > MAX_ORCHESTRATION_STEPS:
            raise ValueError("scripted model gateway exceeds the orchestration step limit")
        self._next_step = 0
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelAction:
        self.requests.append(request)
        if self._next_step >= len(self._steps):
            raise MalformedModelOutputError()

        step = self._steps[self._next_step]
        self._next_step += 1
        if isinstance(step, ModelGatewayError):
            raise step
        return step
