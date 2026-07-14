from datetime import UTC, datetime
from uuid import uuid4

import pytest

from switchboard.domain.agents import AgentDefinition, AgentVersion
from switchboard.domain.context import ContextPolicy
from switchboard.domain.errors import DomainValidationError
from switchboard.domain.identifiers import (
    AgentDefinitionId,
    AgentVersionId,
    TeamId,
)


def test_agent_definition_normalizes_name() -> None:
    agent = AgentDefinition(
        id=AgentDefinitionId(uuid4()),
        team_id=TeamId(uuid4()),
        name="  Project Assistant  ",
        created_at=datetime(2026, 7, 13, tzinfo=UTC),
    )

    assert agent.name == "Project Assistant"


def test_agent_definition_rejects_blank_name() -> None:
    with pytest.raises(
        DomainValidationError,
        match="name must not be blank",
    ):
        AgentDefinition(
            id=AgentDefinitionId(uuid4()),
            team_id=TeamId(uuid4()),
            name="   ",
            created_at=datetime(2026, 7, 13, tzinfo=UTC),
        )


def test_agent_version_requires_positive_version_number() -> None:
    with pytest.raises(
        DomainValidationError,
        match="version_number must be greater than zero",
    ):
        AgentVersion(
            id=AgentVersionId(uuid4()),
            agent_definition_id=AgentDefinitionId(uuid4()),
            version_number=0,
            context_policy=ContextPolicy(4096, 512, 256, 256, 1),
            created_at=datetime(2026, 7, 13, tzinfo=UTC),
        )
