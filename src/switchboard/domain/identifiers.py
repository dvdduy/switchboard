"""Strongly typed identifiers used by the Switchboard domain."""

from typing import NewType
from uuid import UUID

TeamId = NewType("TeamId", UUID)
ActorId = NewType("ActorId", UUID)

AgentDefinitionId = NewType("AgentDefinitionId", UUID)
AgentVersionId = NewType("AgentVersionId", UUID)
ToolDefinitionId = NewType("ToolDefinitionId", UUID)
ToolVersionId = NewType("ToolVersionId", UUID)
ToolConformanceRunId = NewType("ToolConformanceRunId", UUID)
ToolConformanceCaseResultId = NewType("ToolConformanceCaseResultId", UUID)
AgentToolBindingId = NewType("AgentToolBindingId", UUID)
ToolInvocationId = NewType("ToolInvocationId", UUID)
PolicyEvaluationId = NewType("PolicyEvaluationId", UUID)
ApprovalRequestId = NewType("ApprovalRequestId", UUID)

ConversationId = NewType("ConversationId", UUID)
MessageId = NewType("MessageId", UUID)
ConversationSummaryId = NewType("ConversationSummaryId", UUID)
CommandReceiptId = NewType("CommandReceiptId", UUID)

TurnId = NewType("TurnId", UUID)
TurnAttemptId = NewType("TurnAttemptId", UUID)
ExecutionEventId = NewType("ExecutionEventId", UUID)
