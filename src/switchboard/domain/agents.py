"""Agent definition and immutable agent-version entities."""

from dataclasses import dataclass
from datetime import datetime

from switchboard.domain.common import (
    normalize_utc,
    require_not_blank,
    require_positive,
)
from switchboard.domain.identifiers import (
    AgentDefinitionId,
    AgentVersionId,
    TeamId,
)


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    """Stable identity for one product-owned agent."""

    id: AgentDefinitionId
    team_id: TeamId
    name: str
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "name",
            require_not_blank(self.name, field_name="name"),
        )
        object.__setattr__(
            self,
            "created_at",
            normalize_utc(
                self.created_at,
                field_name="created_at",
            ),
        )


@dataclass(frozen=True, slots=True)
class AgentVersion:
    """Immutable version of an agent configuration."""

    id: AgentVersionId
    agent_definition_id: AgentDefinitionId
    version_number: int
    created_at: datetime

    def __post_init__(self) -> None:
        require_positive(
            self.version_number,
            field_name="version_number",
        )
        object.__setattr__(
            self,
            "created_at",
            normalize_utc(
                self.created_at,
                field_name="created_at",
            ),
        )
