"""Bounded ephemeral LangGraph adapter for one Switchboard agent turn."""

import json
from dataclasses import dataclass
from typing import Literal, TypedDict, cast
from uuid import UUID

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from switchboard.application.errors import (
    MalformedModelOutputError,
    OrchestrationStepLimitError,
)
from switchboard.application.ports.agent_orchestrator import (
    MAX_ORCHESTRATION_STEPS,
    OrchestrationRequest,
    OrchestrationResult,
    ToolCallAwaitingApproval,
    ToolCallHandler,
)
from switchboard.application.ports.model_gateway import (
    CallTool,
    ModelGateway,
    ModelRequest,
    ModelRequestPhase,
    ModelToolResult,
    Respond,
)
from switchboard.domain.errors import DomainValidationError
from switchboard.domain.identifiers import (
    ApprovalRequestId,
    ToolInvocationId,
    ToolVersionId,
)
from switchboard.domain.json_values import canonical_json

_GRAPH_RECURSION_LIMIT = MAX_ORCHESTRATION_STEPS + 2


class OrchestrationGraphState(TypedDict, total=False):
    """JSON-compatible ephemeral values owned only by the graph adapter."""

    steps: int
    route: Literal["respond", "tool"]
    response_text: str
    tool_called: bool
    tool_version_id: str
    tool_arguments: dict[str, object]
    tool_output: dict[str, object]
    post_tool_route: Literal["result", "approval"]
    approval_id: str
    invocation_id: str
    approval_event_sequence: int


@dataclass(frozen=True, slots=True)
class _RunContext:
    request: OrchestrationRequest
    tool_handler: ToolCallHandler


class LangGraphAgentOrchestrator:
    """Coordinate a direct response or one durable read-only tool call."""

    def __init__(self, *, model_gateway: ModelGateway) -> None:
        self._model_gateway = model_gateway
        builder = StateGraph(OrchestrationGraphState, context_schema=_RunContext)
        builder.add_node("request_initial_action", self._request_initial_action)
        builder.add_node("dispatch_tool", self._dispatch_tool)
        builder.add_node("request_final_response", self._request_final_response)
        builder.add_edge(START, "request_initial_action")
        builder.add_conditional_edges(
            "request_initial_action",
            self._route_initial_action,
            {
                "respond": END,
                "tool": "dispatch_tool",
            },
        )
        builder.add_conditional_edges(
            "dispatch_tool",
            self._route_after_tool,
            {
                "result": "request_final_response",
                "approval": END,
            },
        )
        builder.add_edge("request_final_response", END)
        self._graph = builder.compile()

    async def run(
        self,
        request: OrchestrationRequest,
        *,
        tool_handler: ToolCallHandler,
    ) -> OrchestrationResult:
        state = await self._graph.ainvoke(
            OrchestrationGraphState(steps=0, tool_called=False),
            config={"recursion_limit": _GRAPH_RECURSION_LIMIT},
            context=_RunContext(request=request, tool_handler=tool_handler),
        )
        state = cast(OrchestrationGraphState, state)
        response_text = state.get("response_text")
        approval_required = _approval_outcome(state)
        if response_text is not None and not isinstance(response_text, str):
            raise MalformedModelOutputError()
        return OrchestrationResult(
            response_text=response_text,
            tool_called=state.get("tool_called") is True,
            approval_required=approval_required,
        )

    async def _request_initial_action(
        self,
        state: OrchestrationGraphState,
        runtime: Runtime[_RunContext],
    ) -> OrchestrationGraphState:
        steps = self._next_step(state, runtime.context.request)
        action = await self._model_gateway.generate(runtime.context.request.initial_model_request)
        if isinstance(action, Respond):
            return OrchestrationGraphState(
                steps=steps,
                route="respond",
                response_text=action.text,
                tool_called=False,
            )
        if isinstance(action, CallTool):
            return OrchestrationGraphState(
                steps=steps,
                route="tool",
                tool_called=True,
                tool_version_id=str(action.tool_version_id),
                tool_arguments=_mutable_json_object(action.arguments),
            )
        raise MalformedModelOutputError()

    @staticmethod
    def _route_initial_action(
        state: OrchestrationGraphState,
    ) -> Literal["respond", "tool"]:
        route = state.get("route")
        if route not in {"respond", "tool"}:
            raise MalformedModelOutputError()
        return route

    async def _dispatch_tool(
        self,
        state: OrchestrationGraphState,
        runtime: Runtime[_RunContext],
    ) -> OrchestrationGraphState:
        steps = self._next_step(state, runtime.context.request)
        tool_version_id = state.get("tool_version_id")
        tool_arguments = state.get("tool_arguments")
        if not isinstance(tool_version_id, str) or not isinstance(tool_arguments, dict):
            raise MalformedModelOutputError()
        try:
            version_id = ToolVersionId(UUID(tool_version_id))
        except ValueError:
            raise MalformedModelOutputError() from None
        result = await runtime.context.tool_handler.execute(
            CallTool(tool_version_id=version_id, arguments=tool_arguments)
        )
        if isinstance(result, ToolCallAwaitingApproval):
            return OrchestrationGraphState(
                steps=steps,
                post_tool_route="approval",
                approval_id=str(result.approval_id),
                invocation_id=str(result.invocation_id),
                approval_event_sequence=result.event_sequence,
            )
        if result.tool_version_id != version_id:
            raise MalformedModelOutputError()
        return OrchestrationGraphState(
            steps=steps,
            post_tool_route="result",
            tool_output=_mutable_json_object(result.output),
        )

    @staticmethod
    def _route_after_tool(
        state: OrchestrationGraphState,
    ) -> Literal["result", "approval"]:
        route = state.get("post_tool_route")
        if route not in {"result", "approval"}:
            raise MalformedModelOutputError()
        return route

    async def _request_final_response(
        self,
        state: OrchestrationGraphState,
        runtime: Runtime[_RunContext],
    ) -> OrchestrationGraphState:
        steps = self._next_step(state, runtime.context.request)
        tool_version_id = state.get("tool_version_id")
        tool_output = state.get("tool_output")
        if not isinstance(tool_version_id, str) or not isinstance(tool_output, dict):
            raise MalformedModelOutputError()
        try:
            version_id = ToolVersionId(UUID(tool_version_id))
        except ValueError:
            raise MalformedModelOutputError() from None
        final_request = ModelRequest(
            phase=ModelRequestPhase.FINAL,
            context=runtime.context.request.initial_model_request.context,
            tool_result=ModelToolResult(
                tool_version_id=version_id,
                output=tool_output,
            ),
        )
        action = await self._model_gateway.generate(final_request)
        if not isinstance(action, Respond):
            raise MalformedModelOutputError()
        return OrchestrationGraphState(
            steps=steps,
            response_text=action.text,
        )

    @staticmethod
    def _next_step(
        state: OrchestrationGraphState,
        request: OrchestrationRequest,
    ) -> int:
        steps = state.get("steps", 0) + 1
        if steps > request.max_steps:
            raise OrchestrationStepLimitError()
        return steps


def _mutable_json_object(value: object) -> dict[str, object]:
    parsed = json.loads(canonical_json(value))
    if not isinstance(parsed, dict):
        raise MalformedModelOutputError()
    return cast(dict[str, object], parsed)


def _approval_outcome(
    state: OrchestrationGraphState,
) -> ToolCallAwaitingApproval | None:
    approval_id = state.get("approval_id")
    invocation_id = state.get("invocation_id")
    event_sequence = state.get("approval_event_sequence")
    if approval_id is None and invocation_id is None and event_sequence is None:
        return None
    if (
        not isinstance(approval_id, str)
        or not isinstance(invocation_id, str)
        or not isinstance(event_sequence, int)
    ):
        raise MalformedModelOutputError()
    try:
        return ToolCallAwaitingApproval(
            approval_id=ApprovalRequestId(UUID(approval_id)),
            invocation_id=ToolInvocationId(UUID(invocation_id)),
            event_sequence=event_sequence,
        )
    except (ValueError, DomainValidationError):
        raise MalformedModelOutputError() from None
