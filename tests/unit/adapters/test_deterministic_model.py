import pytest

from switchboard.adapters.models.deterministic import ScriptedModelGateway
from switchboard.application.errors import (
    MalformedModelOutputError,
    ModelGatewayUnavailableError,
)
from switchboard.application.ports.agent_orchestrator import MAX_ORCHESTRATION_STEPS
from switchboard.application.ports.model_gateway import (
    ModelContextItem,
    ModelRequest,
    ModelRequestPhase,
    Respond,
)
from switchboard.domain.context import ContextItemKind
from switchboard.domain.conversations import MessageRole


def request() -> ModelRequest:
    return ModelRequest(
        phase=ModelRequestPhase.INITIAL,
        context=(
            ModelContextItem(
                kind=ContextItemKind.MESSAGE,
                content="Hello",
                role=MessageRole.USER,
            ),
        ),
    )


async def test_scripted_gateway_returns_actions_and_records_requests() -> None:
    first = Respond("First response")
    second = Respond("Second response")
    gateway = ScriptedModelGateway((first, second))
    model_request = request()

    assert await gateway.generate(model_request) == first
    assert await gateway.generate(model_request) == second
    assert gateway.requests == [model_request, model_request]


async def test_scripted_gateway_raises_safe_scripted_failure() -> None:
    gateway = ScriptedModelGateway((ModelGatewayUnavailableError(),))

    with pytest.raises(ModelGatewayUnavailableError, match="gateway is unavailable"):
        await gateway.generate(request())


async def test_exhausted_script_is_a_safe_malformed_output() -> None:
    gateway = ScriptedModelGateway((Respond("Only response"),))
    await gateway.generate(request())

    with pytest.raises(MalformedModelOutputError, match="structured action contract"):
        await gateway.generate(request())


def test_scripted_gateway_rejects_an_empty_script() -> None:
    with pytest.raises(ValueError, match="at least one step"):
        ScriptedModelGateway(())


def test_scripted_gateway_rejects_an_unbounded_script() -> None:
    with pytest.raises(ValueError, match="step limit"):
        ScriptedModelGateway(Respond(str(index)) for index in range(MAX_ORCHESTRATION_STEPS + 1))
