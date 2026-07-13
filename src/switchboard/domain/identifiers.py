"""Strongly typed identifiers used by the Switchboard domain."""

from typing import NewType
from uuid import UUID

TeamId = NewType("TeamId", UUID)

AgentDefinitionId = NewType("AgentDefinitionId", UUID)
AgentVersionId = NewType("AgentVersionId", UUID)

ConversationId = NewType("ConversationId", UUID)
MessageId = NewType("MessageId", UUID)

TurnId = NewType("TurnId", UUID)
TurnAttemptId = NewType("TurnAttemptId", UUID)
