"""Repository contracts required by application use cases."""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from switchboard.domain.agents import AgentDefinition, AgentVersion
from switchboard.domain.approvals import ApprovalRequest, PolicyEvaluationRecord
from switchboard.domain.command_receipts import CommandOperation, CommandReceipt
from switchboard.domain.context import ConversationSummary
from switchboard.domain.conversations import Conversation, Message, MessageRole
from switchboard.domain.execution_events import (
    ExecutionEvent,
    ExecutionEventKind,
)
from switchboard.domain.identifiers import (
    AgentDefinitionId,
    AgentToolBindingId,
    AgentVersionId,
    ApprovalRequestId,
    ConversationId,
    ExecutionEventId,
    MessageId,
    PolicyEvaluationId,
    TeamId,
    ToolConformanceRunId,
    ToolDefinitionId,
    ToolInvocationId,
    ToolVersionId,
    TurnAttemptId,
    TurnId,
    TurnWorkflowId,
    WorkflowPlanApprovalId,
    WorkflowStepId,
)
from switchboard.domain.tool_invocations import ToolInvocation
from switchboard.domain.tools import (
    AgentToolBinding,
    EligibleTool,
    ToolConformanceCaseResult,
    ToolConformanceRun,
    ToolDefinition,
    ToolManifest,
    ToolVersion,
    ToolVersionState,
)
from switchboard.domain.turns import Turn, TurnAttempt
from switchboard.domain.workflow_approvals import WorkflowPlanApproval
from switchboard.domain.workflows import TurnWorkflow, WorkflowStep


class AgentRepository(Protocol):
    """Persistence operations for agents and immutable versions."""

    async def add_definition(
        self,
        definition: AgentDefinition,
    ) -> None:
        """Persist an agent definition."""

    async def add_version(
        self,
        version: AgentVersion,
    ) -> None:
        """Persist an immutable agent version."""

    async def add_next_version_from(
        self,
        *,
        agent_version_id: AgentVersionId,
        base_version: AgentVersion,
        created_at: datetime,
    ) -> AgentVersion:
        """Lock the definition and clone the base policy into its next version."""

    async def get_definition(
        self,
        agent_definition_id: AgentDefinitionId,
    ) -> AgentDefinition | None:
        """Return an agent definition when it exists."""

    async def get_version(
        self,
        agent_version_id: AgentVersionId,
    ) -> AgentVersion | None:
        """Return an agent version when it exists."""


class ConversationRepository(Protocol):
    """Persistence operations for conversations and ordered messages."""

    async def add(
        self,
        conversation: Conversation,
    ) -> None:
        """Persist a new conversation."""

    async def get(
        self,
        conversation_id: ConversationId,
    ) -> Conversation | None:
        """Return a conversation when it exists."""

    async def append_message(
        self,
        *,
        conversation_id: ConversationId,
        message_id: MessageId,
        role: MessageRole,
        content: str,
        created_at: datetime,
    ) -> Message:
        """Lock the conversation and append its next ordered message."""

    async def get_message(
        self,
        *,
        conversation_id: ConversationId,
        message_id: MessageId,
    ) -> Message | None:
        """Return a message only when it belongs to the conversation."""

    async def list_messages(
        self,
        conversation_id: ConversationId,
    ) -> tuple[Message, ...]:
        """Return committed messages in deterministic sequence order."""

    async def list_messages_through(
        self,
        *,
        conversation_id: ConversationId,
        through_sequence: int,
    ) -> tuple[Message, ...]:
        """Return committed messages through an inclusive sequence cutoff."""

    async def list_messages_after(
        self,
        *,
        conversation_id: ConversationId,
        after_sequence: int,
        limit: int,
    ) -> tuple[Message, ...]:
        """Return a bounded ordered page after an exclusive sequence cursor."""


class CommandReceiptRepository(Protocol):
    """Persistence authority for durable command idempotency."""

    async def add_or_get(
        self,
        receipt: CommandReceipt,
    ) -> tuple[CommandReceipt, bool]:
        """Insert a receipt or return the concurrent authority and creation flag."""

    async def get_by_authority(
        self,
        *,
        team_id: TeamId,
        operation: CommandOperation,
        command_scope: str,
        idempotency_key_hash: str,
    ) -> CommandReceipt | None:
        """Return one receipt by its team/operation/scope/key authority."""


class ConversationSummaryRepository(Protocol):
    """Persistence operations for immutable derived conversation summaries."""

    async def add_if_absent(
        self,
        summary: ConversationSummary,
    ) -> ConversationSummary:
        """Persist a summary or return the concurrent authority winner."""

    async def get_latest_compatible(
        self,
        *,
        conversation_id: ConversationId,
        agent_version_id: AgentVersionId,
        through_sequence: int,
        summarizer_version: str,
        token_counter_version: str,
    ) -> ConversationSummary | None:
        """Return the newest compatible prefix not beyond the cutoff."""


class TurnRepository(Protocol):
    """Persistence operations for turns, attempts, and execution events."""

    async def add(
        self,
        turn: Turn,
    ) -> None:
        """Persist a new logical turn."""

    async def get(
        self,
        turn_id: TurnId,
    ) -> Turn | None:
        """Return a turn when it exists."""

    async def get_for_update(self, turn_id: TurnId) -> Turn | None:
        """Lock and return a turn for a short execution-authority transaction."""

    async def update_turn_lifecycle(
        self,
        *,
        previous: Turn,
        updated: Turn,
    ) -> None:
        """Persist a lifecycle transition using compare-and-set."""

    async def add_attempt(
        self,
        attempt: TurnAttempt,
    ) -> None:
        """Persist a new physical execution attempt."""

    async def get_attempt(
        self,
        attempt_id: TurnAttemptId,
    ) -> TurnAttempt | None:
        """Return a physical execution attempt when it exists."""

    async def update_attempt_lifecycle(
        self,
        *,
        previous: TurnAttempt,
        updated: TurnAttempt,
    ) -> None:
        """Persist an attempt transition using compare-and-set."""

    async def list_attempts(
        self,
        turn_id: TurnId,
    ) -> tuple[TurnAttempt, ...]:
        """Return attempts ordered by attempt number."""

    async def append_event(
        self,
        *,
        turn_id: TurnId,
        event_id: ExecutionEventId,
        attempt_id: TurnAttemptId | None,
        kind: ExecutionEventKind,
        payload: Mapping[str, object],
        occurred_at: datetime,
    ) -> ExecutionEvent:
        """Lock the turn and append its next durable event."""

    async def list_events(
        self,
        *,
        turn_id: TurnId,
        after_sequence: int,
        limit: int,
    ) -> tuple[ExecutionEvent, ...]:
        """Return events after an exclusive sequence cursor."""


class ToolInvocationRepository(Protocol):
    """Persistence operations for durable logical tool invocations."""

    async def add(self, invocation: ToolInvocation) -> None:
        """Persist one pending invocation before dispatch."""

    async def get(self, invocation_id: ToolInvocationId) -> ToolInvocation | None:
        """Return one invocation by identity."""

    async def list_for_turn(self, turn_id: TurnId) -> tuple[ToolInvocation, ...]:
        """Return invocations ordered by their turn-local number."""

    async def update_lifecycle(
        self,
        *,
        previous: ToolInvocation,
        updated: ToolInvocation,
    ) -> None:
        """Persist a focused invocation lifecycle compare-and-set update."""


class WorkflowRepository(Protocol):
    """Persistence operations for durable workflow progress."""

    async def add(self, workflow: TurnWorkflow) -> None:
        """Persist one workflow before its discovery action."""

    async def get(self, workflow_id: TurnWorkflowId) -> TurnWorkflow | None:
        """Return one workflow by identity."""

    async def get_for_turn(self, turn_id: TurnId) -> TurnWorkflow | None:
        """Return the one Day 9 workflow owned by a turn."""

    async def get_for_turn_for_update(self, turn_id: TurnId) -> TurnWorkflow | None:
        """Lock and return a turn's workflow when it exists."""

    async def update_lifecycle(
        self,
        *,
        previous: TurnWorkflow,
        updated: TurnWorkflow,
    ) -> None:
        """Persist focused workflow lifecycle state with compare-and-set."""

    async def add_step(self, step: WorkflowStep) -> None:
        """Persist a typed step while its workflow plan is still extensible."""

    async def get_step(self, step_id: WorkflowStepId) -> WorkflowStep | None:
        """Return one workflow step by identity."""

    async def list_steps(self, workflow_id: TurnWorkflowId) -> tuple[WorkflowStep, ...]:
        """Return workflow steps in positive plan order."""

    async def update_step_lifecycle(
        self,
        *,
        previous: WorkflowStep,
        updated: WorkflowStep,
    ) -> None:
        """Persist focused step lifecycle state with compare-and-set."""


class WorkflowPlanApprovalRepository(Protocol):
    """Persistence operations for exact frozen-plan approvals."""

    async def add(self, approval: WorkflowPlanApproval) -> None:
        """Persist one pending approval for a frozen workflow plan."""

    async def get(
        self,
        approval_id: WorkflowPlanApprovalId,
    ) -> WorkflowPlanApproval | None:
        """Return one workflow-plan approval by identity."""

    async def get_for_workflow(
        self,
        workflow_id: TurnWorkflowId,
    ) -> WorkflowPlanApproval | None:
        """Return the one plan approval owned by a workflow."""

    async def get_for_update(
        self,
        approval_id: WorkflowPlanApprovalId,
    ) -> WorkflowPlanApproval | None:
        """Lock and return one plan approval for a short decision transaction."""

    async def update_lifecycle(
        self,
        *,
        previous: WorkflowPlanApproval,
        updated: WorkflowPlanApproval,
    ) -> None:
        """Persist a focused plan-approval lifecycle compare-and-set update."""


class ApprovalRepository(Protocol):
    """Persistence operations for immutable policy audit and durable approval."""

    async def add_evaluation(self, evaluation: PolicyEvaluationRecord) -> None:
        """Persist one immutable policy evaluation."""

    async def get_evaluation(
        self,
        evaluation_id: PolicyEvaluationId,
    ) -> PolicyEvaluationRecord | None:
        """Return one immutable policy evaluation."""

    async def list_evaluations_for_invocation(
        self,
        invocation_id: ToolInvocationId,
    ) -> tuple[PolicyEvaluationRecord, ...]:
        """Return invocation evaluations in durable order."""

    async def add_request(self, approval: ApprovalRequest) -> None:
        """Persist one pending fingerprint-bound approval request."""

    async def get_request(self, approval_id: ApprovalRequestId) -> ApprovalRequest | None:
        """Return one approval request by identity."""

    async def get_request_for_update(
        self,
        approval_id: ApprovalRequestId,
    ) -> ApprovalRequest | None:
        """Lock and return one approval request for a short decision transaction."""

    async def list_requests_for_invocation(
        self,
        invocation_id: ToolInvocationId,
    ) -> tuple[ApprovalRequest, ...]:
        """Return invocation approval requests in durable order."""

    async def update_lifecycle(
        self,
        *,
        previous: ApprovalRequest,
        updated: ApprovalRequest,
    ) -> None:
        """Persist a focused approval lifecycle compare-and-set update."""


class ToolRegistryRepository(Protocol):
    """Persistence operations for versioned tool registry state."""

    async def add_definition(self, definition: ToolDefinition) -> None:
        """Persist one stable team-owned tool identity."""

    async def add_definition_if_absent(self, definition: ToolDefinition) -> bool:
        """Persist a definition unless its team/key identity already exists."""

    async def get_definition(
        self,
        tool_definition_id: ToolDefinitionId,
    ) -> ToolDefinition | None:
        """Return a definition by identity."""

    async def get_definition_by_key(
        self,
        *,
        team_id: TeamId,
        tool_key: str,
    ) -> ToolDefinition | None:
        """Return one team's definition for a normalized stable key."""

    async def add_next_version(
        self,
        *,
        tool_version_id: ToolVersionId,
        tool_definition_id: ToolDefinitionId,
        manifest: ToolManifest,
        created_at: datetime,
    ) -> ToolVersion:
        """Lock a definition and persist its next positive version number."""

    async def get_version(self, tool_version_id: ToolVersionId) -> ToolVersion | None:
        """Return immutable version content by identity."""

    async def get_version_state(
        self,
        tool_version_id: ToolVersionId,
    ) -> ToolVersionState | None:
        """Return separately mutable lifecycle state."""

    async def get_version_state_for_update(
        self,
        tool_version_id: ToolVersionId,
    ) -> ToolVersionState | None:
        """Lock lifecycle state for an atomic eligibility-dependent write."""

    async def update_version_state(
        self,
        *,
        previous: ToolVersionState,
        updated: ToolVersionState,
    ) -> None:
        """Persist one lifecycle transition using revision compare-and-set."""

    async def add_binding(self, binding: AgentToolBinding) -> None:
        """Persist an immutable exact-version agent binding."""

    async def get_binding(
        self,
        binding_id: AgentToolBindingId,
    ) -> AgentToolBinding | None:
        """Return a binding by identity."""

    async def list_bindings(
        self,
        agent_version_id: AgentVersionId,
    ) -> tuple[AgentToolBinding, ...]:
        """Return deterministic bindings for an immutable agent version."""

    async def list_eligible_for_agent(
        self,
        *,
        team_id: TeamId,
        agent_version_id: AgentVersionId,
    ) -> tuple[EligibleTool, ...]:
        """Return active, bound, successful exact-version manifests."""

    async def add_conformance_run(
        self,
        run: ToolConformanceRun,
        case_results: tuple[ToolConformanceCaseResult, ...],
    ) -> None:
        """Persist one complete run and all case results atomically."""

    async def get_conformance_run(
        self,
        run_id: ToolConformanceRunId,
    ) -> tuple[ToolConformanceRun, tuple[ToolConformanceCaseResult, ...]] | None:
        """Return one run with ordered case results."""
