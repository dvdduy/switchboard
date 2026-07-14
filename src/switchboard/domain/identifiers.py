"""Strongly typed identifiers used by the Switchboard domain."""

from typing import NewType
from uuid import UUID

TeamId = NewType("TeamId", UUID)

AgentDefinitionId = NewType("AgentDefinitionId", UUID)
AgentVersionId = NewType("AgentVersionId", UUID)
ToolDefinitionId = NewType("ToolDefinitionId", UUID)
ToolVersionId = NewType("ToolVersionId", UUID)
ToolConformanceRunId = NewType("ToolConformanceRunId", UUID)
ToolConformanceCaseResultId = NewType("ToolConformanceCaseResultId", UUID)
AgentToolBindingId = NewType("AgentToolBindingId", UUID)

ConversationId = NewType("ConversationId", UUID)
MessageId = NewType("MessageId", UUID)
ConversationSummaryId = NewType("ConversationSummaryId", UUID)

TurnId = NewType("TurnId", UUID)
TurnAttemptId = NewType("TurnAttemptId", UUID)
ExecutionEventId = NewType("ExecutionEventId", UUID)
