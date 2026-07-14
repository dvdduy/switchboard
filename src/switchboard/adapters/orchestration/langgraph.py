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
from switchboard.domain.identifiers import ToolVersionId
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
        builder.add_edge("dispatch_tool", "request_final_response")
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
        response_text = state.get("response_text")
        if not isinstance(response_text, str):
            raise MalformedModelOutputError()
        return OrchestrationResult(
            response_text=response_text,
            tool_called=state.get("tool_called") is True,
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
        if result.tool_version_id != version_id:
            raise MalformedModelOutputError()
        return OrchestrationGraphState(
            steps=steps,
            tool_output=_mutable_json_object(result.output),
        )

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
