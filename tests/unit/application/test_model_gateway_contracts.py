from uuid import uuid4

import pytest

from switchboard.application.ports.agent_orchestrator import (
    MAX_ORCHESTRATION_STEPS,
    OrchestrationRequest,
    OrchestrationResult,
)
from switchboard.application.ports.model_gateway import (
    MAX_MODEL_CONTEXT_ITEMS,
    MAX_MODEL_RESPONSE_CHARS,
    MAX_TOOL_ARGUMENT_JSON_CHARS,
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
from switchboard.domain.errors import DomainValidationError
from switchboard.domain.identifiers import ToolDefinitionId, ToolVersionId


def context_item(content: str = "Find overdue work.") -> ModelContextItem:
    return ModelContextItem(
        kind=ContextItemKind.MESSAGE,
        content=content,
        role=MessageRole.USER,
    )


def tool_descriptor() -> ModelToolDescriptor:
    return ModelToolDescriptor(
        tool_definition_id=ToolDefinitionId(uuid4()),
        tool_version_id=ToolVersionId(uuid4()),
        tool_key="search_work_items",
        display_name="Search work items",
        description="Search synthetic work items.",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
        },
    )


def test_model_contracts_freeze_normalized_json_values() -> None:
    descriptor = tool_descriptor()
    arguments = {"query": "overdue", "filters": ["open"]}
    output = {"items": [{"id": "WI-1"}]}

    action = CallTool(tool_version_id=descriptor.tool_version_id, arguments=arguments)
    result = ModelToolResult(tool_version_id=descriptor.tool_version_id, output=output)
    final = ModelRequest(
        phase=ModelRequestPhase.FINAL,
        context=(context_item(),),
        tool_result=result,
    )

    arguments["query"] = "changed"
    output["items"] = []

    assert action.arguments == {"query": "overdue", "filters": ("open",)}
    assert result.output == {"items": ({"id": "WI-1"},)}
    assert final.context == (context_item(),)


def test_model_contracts_preserve_nonblank_text_exactly() -> None:
    item = context_item("  Preserve context whitespace.\n")
    response = Respond("  Preserve response whitespace.\n")
    result = OrchestrationResult(
        response_text="  Preserve final whitespace.\n",
        tool_called=False,
    )

    assert item.content == "  Preserve context whitespace.\n"
    assert response.text == "  Preserve response whitespace.\n"
    assert result.response_text == "  Preserve final whitespace.\n"


def test_model_request_enforces_phase_and_candidate_invariants() -> None:
    descriptor = tool_descriptor()
    result = ModelToolResult(tool_version_id=descriptor.tool_version_id, output={})

    with pytest.raises(DomainValidationError, match="initial.*tool result"):
        ModelRequest(
            phase=ModelRequestPhase.INITIAL,
            context=(context_item(),),
            tool_result=result,
        )
    with pytest.raises(DomainValidationError, match="final.*requires"):
        ModelRequest(phase=ModelRequestPhase.FINAL, context=(context_item(),))
    with pytest.raises(DomainValidationError, match="final.*must not expose"):
        ModelRequest(
            phase=ModelRequestPhase.FINAL,
            context=(context_item(),),
            tools=(descriptor,),
            tool_result=result,
        )
    with pytest.raises(DomainValidationError, match="versions must be unique"):
        ModelRequest(
            phase=ModelRequestPhase.INITIAL,
            context=(context_item(),),
            tools=(descriptor, descriptor),
        )


def test_model_request_and_actions_enforce_hard_size_bounds() -> None:
    with pytest.raises(DomainValidationError, match="too many context"):
        ModelRequest(
            phase=ModelRequestPhase.INITIAL,
            context=tuple(context_item(str(index)) for index in range(MAX_MODEL_CONTEXT_ITEMS + 1)),
        )
    with pytest.raises(DomainValidationError, match="response is too long"):
        Respond("x" * (MAX_MODEL_RESPONSE_CHARS + 1))
    with pytest.raises(DomainValidationError, match="arguments are too large"):
        CallTool(
            tool_version_id=ToolVersionId(uuid4()),
            arguments={"value": "x" * MAX_TOOL_ARGUMENT_JSON_CHARS},
        )


def test_context_role_and_orchestration_bounds_are_explicit() -> None:
    with pytest.raises(DomainValidationError, match="message context requires"):
        ModelContextItem(
            kind=ContextItemKind.MESSAGE,
            content="Message",
            role=None,
        )
    with pytest.raises(DomainValidationError, match="summary.*must not have"):
        ModelContextItem(
            kind=ContextItemKind.SUMMARY,
            content="Summary",
            role=MessageRole.USER,
        )

    initial = ModelRequest(phase=ModelRequestPhase.INITIAL, context=(context_item(),))
    assert OrchestrationRequest(initial).max_steps == 4
    with pytest.raises(DomainValidationError, match="max_steps"):
        OrchestrationRequest(initial, max_steps=MAX_ORCHESTRATION_STEPS + 1)
    with pytest.raises(DomainValidationError, match="response_text"):
        OrchestrationResult(response_text=" ", tool_called=False)
