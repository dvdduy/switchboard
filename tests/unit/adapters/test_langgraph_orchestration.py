import asyncio
import json
from uuid import uuid4

import pytest

from switchboard.adapters.models.deterministic import ScriptedModelGateway
from switchboard.adapters.orchestration.langgraph import (
    LangGraphAgentOrchestrator,
    OrchestrationGraphState,
)
from switchboard.application.errors import (
    MalformedModelOutputError,
    ModelGatewayUnavailableError,
    OrchestrationStepLimitError,
)
from switchboard.application.ports.agent_orchestrator import OrchestrationRequest
from switchboard.application.ports.model_gateway import (
    CallTool,
    ModelContextItem,
    ModelRequest,
    ModelRequestPhase,
    ModelToolDescriptor,
    ModelToolResult,
    Respond,
)
from switchboard.domain.context import ContextItemKind
from switchboard.domain.conversations import MessageRole
from switchboard.domain.identifiers import ToolDefinitionId, ToolVersionId


class RecordingToolHandler:
    def __init__(self, output: dict[str, object] | None = None) -> None:
        self.output = {"items": [{"id": "WI-1"}]} if output is None else output
        self.actions: list[CallTool] = []

    async def execute(self, action: CallTool) -> ModelToolResult:
        self.actions.append(action)
        return ModelToolResult(tool_version_id=action.tool_version_id, output=self.output)


class MismatchedToolHandler:
    async def execute(self, action: CallTool) -> ModelToolResult:
        del action
        return ModelToolResult(tool_version_id=ToolVersionId(uuid4()), output={})


def descriptor() -> ModelToolDescriptor:
    return ModelToolDescriptor(
        tool_definition_id=ToolDefinitionId(uuid4()),
        tool_version_id=ToolVersionId(uuid4()),
        tool_key="search_work_items",
        display_name="Search work items",
        description="Search deterministic work items.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    )


def initial_request(
    tool: ModelToolDescriptor | None = None,
    *,
    content: str = "Find overdue work.",
) -> ModelRequest:
    return ModelRequest(
        phase=ModelRequestPhase.INITIAL,
        context=(
            ModelContextItem(
                kind=ContextItemKind.MESSAGE,
                content=content,
                role=MessageRole.USER,
            ),
        ),
        tools=() if tool is None else (tool,),
    )


async def test_direct_response_finishes_without_dispatch() -> None:
    gateway = ScriptedModelGateway((Respond("  Direct response.\n"),))
    orchestrator = LangGraphAgentOrchestrator(model_gateway=gateway)
    handler = RecordingToolHandler()

    result = await orchestrator.run(
        OrchestrationRequest(initial_request(), max_steps=1),
        tool_handler=handler,
    )

    assert result.response_text == "  Direct response.\n"
    assert result.tool_called is False
    assert handler.actions == []
    assert gateway.requests == [initial_request()]


async def test_one_tool_call_builds_a_normalized_final_model_request() -> None:
    tool = descriptor()
    call = CallTool(tool_version_id=tool.tool_version_id, arguments={"query": "overdue"})
    gateway = ScriptedModelGateway((call, Respond("One item is overdue.")))
    handler = RecordingToolHandler()
    orchestrator = LangGraphAgentOrchestrator(model_gateway=gateway)

    result = await orchestrator.run(
        OrchestrationRequest(initial_request(tool), max_steps=3),
        tool_handler=handler,
    )

    assert result.response_text == "One item is overdue."
    assert result.tool_called is True
    assert handler.actions == [call]
    assert [request.phase for request in gateway.requests] == [
        ModelRequestPhase.INITIAL,
        ModelRequestPhase.FINAL,
    ]
    final_request = gateway.requests[1]
    assert final_request.context == initial_request(tool).context
    assert final_request.tools == ()
    assert final_request.tool_result == ModelToolResult(
        tool_version_id=tool.tool_version_id,
        output={"items": [{"id": "WI-1"}]},
    )


async def test_second_tool_request_is_rejected_without_a_second_dispatch() -> None:
    tool = descriptor()
    first = CallTool(tool.tool_version_id, {"query": "first"})
    second = CallTool(tool.tool_version_id, {"query": "second"})
    orchestrator = LangGraphAgentOrchestrator(model_gateway=ScriptedModelGateway((first, second)))
    handler = RecordingToolHandler()

    with pytest.raises(MalformedModelOutputError, match="structured action contract"):
        await orchestrator.run(
            OrchestrationRequest(initial_request(tool), max_steps=3),
            tool_handler=handler,
        )

    assert handler.actions == [first]


async def test_mismatched_handler_result_is_rejected_before_final_model_call() -> None:
    tool = descriptor()
    call = CallTool(tool.tool_version_id, {"query": "overdue"})
    gateway = ScriptedModelGateway((call, Respond("must not be used")))

    with pytest.raises(MalformedModelOutputError, match="structured action contract"):
        await LangGraphAgentOrchestrator(model_gateway=gateway).run(
            OrchestrationRequest(initial_request(tool), max_steps=3),
            tool_handler=MismatchedToolHandler(),
        )

    assert gateway.requests == [initial_request(tool)]


async def test_step_limit_blocks_work_before_the_next_bounded_node() -> None:
    tool = descriptor()
    call = CallTool(tool.tool_version_id, {"query": "overdue"})
    handler = RecordingToolHandler()

    with pytest.raises(OrchestrationStepLimitError, match="step limit"):
        await LangGraphAgentOrchestrator(
            model_gateway=ScriptedModelGateway((call, Respond("unused")))
        ).run(
            OrchestrationRequest(initial_request(tool), max_steps=1),
            tool_handler=handler,
        )
    assert handler.actions == []

    with pytest.raises(OrchestrationStepLimitError, match="step limit"):
        await LangGraphAgentOrchestrator(
            model_gateway=ScriptedModelGateway((call, Respond("unused")))
        ).run(
            OrchestrationRequest(initial_request(tool), max_steps=2),
            tool_handler=handler,
        )
    assert handler.actions == [call]


async def test_gateway_failure_remains_a_safe_application_error() -> None:
    orchestrator = LangGraphAgentOrchestrator(
        model_gateway=ScriptedModelGateway((ModelGatewayUnavailableError(),))
    )

    with pytest.raises(ModelGatewayUnavailableError, match="gateway is unavailable"):
        await orchestrator.run(
            OrchestrationRequest(initial_request()),
            tool_handler=RecordingToolHandler(),
        )


class ConcurrentDirectGateway:
    def __init__(self) -> None:
        self.entered = 0
        self.both_entered = asyncio.Event()

    async def generate(self, request: ModelRequest) -> Respond:
        self.entered += 1
        if self.entered == 2:
            self.both_entered.set()
        await self.both_entered.wait()
        return Respond(f"response:{request.context[0].content}")


async def test_concurrent_runs_keep_runtime_context_independent() -> None:
    gateway = ConcurrentDirectGateway()
    orchestrator = LangGraphAgentOrchestrator(model_gateway=gateway)

    first, second = await asyncio.gather(
        orchestrator.run(
            OrchestrationRequest(initial_request(content="first")),
            tool_handler=RecordingToolHandler(),
        ),
        orchestrator.run(
            OrchestrationRequest(initial_request(content="second")),
            tool_handler=RecordingToolHandler(),
        ),
    )

    assert {first.response_text, second.response_text} == {
        "response:first",
        "response:second",
    }


def test_graph_state_contract_is_json_compatible_and_framework_safe() -> None:
    state = OrchestrationGraphState(
        steps=2,
        route="tool",
        response_text="response",
        tool_called=True,
        tool_version_id=str(uuid4()),
        tool_arguments={"query": "overdue"},
        tool_output={"items": [{"id": "WI-1"}]},
    )

    assert json.loads(json.dumps(state)) == state
    annotation_text = repr(OrchestrationGraphState.__annotations__)
    assert "UnitOfWork" not in annotation_text
    assert "ModelRequest" not in annotation_text
    assert "ToolCallHandler" not in annotation_text
