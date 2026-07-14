"""Pure policy evaluation and exact-action approval identity."""

import re
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from switchboard.domain.errors import DomainValidationError
from switchboard.domain.identifiers import (
    ActorId,
    AgentVersionId,
    TeamId,
    ToolDefinitionId,
    ToolVersionId,
)
from switchboard.domain.json_values import JsonObject, canonical_json, freeze_json_object
from switchboard.domain.tools import ToolEffect

POLICY_VERSION = "day8-v1"
ACTION_FINGERPRINT_VERSION = "action-v1"

_SCOPE_PATTERN = re.compile(r"^[a-z][a-z0-9._:-]{0,99}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class PolicyEnvironment(StrEnum):
    """Deployment boundary in which an action is proposed."""

    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class PolicyDecision(StrEnum):
    """Structured result of evaluating one exact proposed action."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"
    REQUIRE_ELEVATED_APPROVAL = "require_elevated_approval"


class PolicyReasonCode(StrEnum):
    """Stable explanation that never incorporates untrusted text."""

    READ_ONLY_ALLOWED = "read_only_allowed"
    MUTATION_CONFIRMATION_REQUIRED = "mutation_confirmation_required"
    TEAM_MISMATCH = "team_mismatch"
    TOOL_NOT_BOUND = "tool_not_bound"
    TOOL_NOT_ACTIVE = "tool_not_active"
    TOOL_NOT_CONFORMANT = "tool_not_conformant"
    SCOPE_DENIED = "scope_denied"
    EXTERNAL_SIDE_EFFECT_DENIED = "external_side_effect_denied"
    PRIVILEGED_DENIED = "privileged_denied"


@dataclass(frozen=True, slots=True)
class PolicyContext:
    """Trusted platform context for one policy evaluation."""

    team_id: TeamId
    actor_id: ActorId
    agent_version_id: AgentVersionId
    tool_team_id: TeamId
    tool_definition_id: ToolDefinitionId
    tool_version_id: ToolVersionId
    effect: ToolEffect
    required_scopes: tuple[str, ...]
    granted_scopes: tuple[str, ...]
    environment: PolicyEnvironment
    arguments: JsonObject
    is_bound: bool
    is_active: bool
    is_conformant: bool

    def __post_init__(self) -> None:
        required_scopes = _normalize_scopes(
            self.required_scopes,
            field_name="required_scopes",
            allow_empty=False,
        )
        granted_scopes = _normalize_scopes(
            self.granted_scopes,
            field_name="granted_scopes",
            allow_empty=True,
        )
        object.__setattr__(self, "required_scopes", required_scopes)
        object.__setattr__(self, "granted_scopes", granted_scopes)
        object.__setattr__(
            self,
            "arguments",
            freeze_json_object(self.arguments, field_name="arguments"),
        )


@dataclass(frozen=True, slots=True)
class PolicyEvaluation:
    """Versioned deterministic policy outcome."""

    policy_version: str
    decision: PolicyDecision
    reason_code: PolicyReasonCode

    def __post_init__(self) -> None:
        if self.policy_version != POLICY_VERSION:
            raise DomainValidationError("policy_version is not supported")
        expected_decision = _REASON_DECISIONS[self.reason_code]
        if self.decision is not expected_decision:
            raise DomainValidationError("reason_code does not match decision")


@dataclass(frozen=True, slots=True)
class ActionFingerprint:
    """Versioned digest binding approval to one exact proposed action."""

    version: str
    digest: str

    def __post_init__(self) -> None:
        if self.version != ACTION_FINGERPRINT_VERSION:
            raise DomainValidationError("fingerprint version is not supported")
        if not _SHA256_PATTERN.fullmatch(self.digest):
            raise DomainValidationError("fingerprint digest must be lowercase SHA-256")


@dataclass(frozen=True, slots=True)
class SafeActionSummary:
    """Value-free action shape suitable for public approval views."""

    tool_definition_id: ToolDefinitionId
    tool_version_id: ToolVersionId
    effect: ToolEffect
    argument_fields: tuple[str, ...]

    def __post_init__(self) -> None:
        fields = tuple(sorted(set(self.argument_fields)))
        if any(not field for field in fields):
            raise DomainValidationError("argument_fields must not contain blank names")
        object.__setattr__(self, "argument_fields", fields)


_REASON_DECISIONS = {
    PolicyReasonCode.READ_ONLY_ALLOWED: PolicyDecision.ALLOW,
    PolicyReasonCode.MUTATION_CONFIRMATION_REQUIRED: PolicyDecision.REQUIRE_CONFIRMATION,
    PolicyReasonCode.TEAM_MISMATCH: PolicyDecision.DENY,
    PolicyReasonCode.TOOL_NOT_BOUND: PolicyDecision.DENY,
    PolicyReasonCode.TOOL_NOT_ACTIVE: PolicyDecision.DENY,
    PolicyReasonCode.TOOL_NOT_CONFORMANT: PolicyDecision.DENY,
    PolicyReasonCode.SCOPE_DENIED: PolicyDecision.DENY,
    PolicyReasonCode.EXTERNAL_SIDE_EFFECT_DENIED: PolicyDecision.DENY,
    PolicyReasonCode.PRIVILEGED_DENIED: PolicyDecision.DENY,
}


def evaluate_policy(context: PolicyContext) -> PolicyEvaluation:
    """Return the first stable policy outcome in enforcement order."""

    if context.team_id != context.tool_team_id:
        return _evaluation(PolicyReasonCode.TEAM_MISMATCH)
    if not context.is_bound:
        return _evaluation(PolicyReasonCode.TOOL_NOT_BOUND)
    if not context.is_active:
        return _evaluation(PolicyReasonCode.TOOL_NOT_ACTIVE)
    if not context.is_conformant:
        return _evaluation(PolicyReasonCode.TOOL_NOT_CONFORMANT)
    if not set(context.required_scopes).issubset(context.granted_scopes):
        return _evaluation(PolicyReasonCode.SCOPE_DENIED)
    if context.effect is ToolEffect.READ_ONLY:
        return _evaluation(PolicyReasonCode.READ_ONLY_ALLOWED)
    if context.effect is ToolEffect.MUTATING:
        return _evaluation(PolicyReasonCode.MUTATION_CONFIRMATION_REQUIRED)
    if context.effect is ToolEffect.EXTERNAL_SIDE_EFFECT:
        return _evaluation(PolicyReasonCode.EXTERNAL_SIDE_EFFECT_DENIED)
    return _evaluation(PolicyReasonCode.PRIVILEGED_DENIED)


def fingerprint_action(
    context: PolicyContext,
    *,
    policy_version: str = POLICY_VERSION,
) -> ActionFingerprint:
    """Fingerprint the exact trusted action envelope using canonical JSON."""

    if policy_version != POLICY_VERSION:
        raise DomainValidationError("policy_version is not supported")
    envelope = {
        "actor_id": str(context.actor_id),
        "agent_version_id": str(context.agent_version_id),
        "arguments": context.arguments,
        "effect": context.effect.value,
        "environment": context.environment.value,
        "policy_version": policy_version,
        "team_id": str(context.team_id),
        "tool_definition_id": str(context.tool_definition_id),
        "tool_version_id": str(context.tool_version_id),
    }
    digest = sha256(canonical_json(envelope).encode("utf-8")).hexdigest()
    return ActionFingerprint(version=ACTION_FINGERPRINT_VERSION, digest=digest)


def summarize_action(context: PolicyContext) -> SafeActionSummary:
    """Derive a public value-free summary without inspecting argument values."""

    return SafeActionSummary(
        tool_definition_id=context.tool_definition_id,
        tool_version_id=context.tool_version_id,
        effect=context.effect,
        argument_fields=tuple(context.arguments),
    )


def _evaluation(reason_code: PolicyReasonCode) -> PolicyEvaluation:
    return PolicyEvaluation(
        policy_version=POLICY_VERSION,
        decision=_REASON_DECISIONS[reason_code],
        reason_code=reason_code,
    )


def _normalize_scopes(
    scopes: tuple[str, ...],
    *,
    field_name: str,
    allow_empty: bool,
) -> tuple[str, ...]:
    normalized = tuple(sorted(set(scopes)))
    if not allow_empty and not normalized:
        raise DomainValidationError(f"{field_name} must not be empty")
    if len(normalized) > 32 or any(not _SCOPE_PATTERN.fullmatch(scope) for scope in normalized):
        raise DomainValidationError(f"{field_name} is invalid")
    return normalized
