"""Versioned public approval read and decision routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, Depends

from switchboard.adapters.api.dependencies import (
    ApprovalApiServices,
    require_actor_id,
    require_idempotency_key,
    require_team_id,
)
from switchboard.adapters.api.v1_models import (
    V1ApprovalDecisionRequest,
    V1ApprovalDecisionResponse,
    V1ApprovalResponse,
    V1ApprovalSafeSummary,
    V1ErrorResponse,
)
from switchboard.application.use_cases.manage_approvals import (
    ApprovalReadModel,
    DecideApprovalCommand,
)
from switchboard.domain.identifiers import ActorId, ApprovalRequestId, TeamId

APPROVAL_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    400: {"model": V1ErrorResponse, "description": "Missing or invalid required header."},
    404: {"model": V1ErrorResponse, "description": "Approval unavailable to this team."},
    409: {"model": V1ErrorResponse, "description": "Decision or lifecycle conflict."},
    422: {"model": V1ErrorResponse, "description": "Invalid request body or path."},
}


def create_approval_router(services: ApprovalApiServices) -> APIRouter:
    router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])

    @router.get(
        "/{approval_id}",
        response_model=V1ApprovalResponse,
        responses=APPROVAL_ERROR_RESPONSES,
    )
    async def get_approval(
        approval_id: UUID,
        team_id: Annotated[TeamId, Depends(require_team_id)],
    ) -> V1ApprovalResponse:
        result = await services.manage.get(
            team_id=team_id,
            approval_id=ApprovalRequestId(approval_id),
        )
        return _approval_response(result)

    @router.post(
        "/{approval_id}/decisions",
        response_model=V1ApprovalDecisionResponse,
        responses=APPROVAL_ERROR_RESPONSES,
    )
    async def decide_approval(
        approval_id: UUID,
        request: Annotated[V1ApprovalDecisionRequest, Body()],
        team_id: Annotated[TeamId, Depends(require_team_id)],
        actor_id: Annotated[ActorId, Depends(require_actor_id)],
        idempotency_key: Annotated[str, Depends(require_idempotency_key)],
    ) -> V1ApprovalDecisionResponse:
        result = await services.manage.decide(
            DecideApprovalCommand(
                team_id=team_id,
                actor_id=actor_id,
                approval_id=ApprovalRequestId(approval_id),
                decision=request.decision,
                idempotency_key=idempotency_key,
            )
        )
        return V1ApprovalDecisionResponse(
            **_approval_response(result.approval).model_dump(),
            invocation_status=result.invocation_status,
        )

    return router


def _approval_response(approval: ApprovalReadModel) -> V1ApprovalResponse:
    return V1ApprovalResponse(
        approval_id=approval.approval_id,
        invocation_id=approval.invocation_id,
        requester_actor_id=approval.requester_actor_id,
        status=approval.status,
        safe_summary=V1ApprovalSafeSummary(
            tool_definition_id=approval.tool_definition_id,
            tool_version_id=approval.tool_version_id,
            effect=approval.effect,
            argument_fields=approval.argument_fields,
        ),
        fingerprint_version=approval.fingerprint_version,
        created_at=approval.created_at,
        expires_at=approval.expires_at,
        resolved_by_actor_id=approval.resolved_by_actor_id,
        resolved_at=approval.resolved_at,
    )
