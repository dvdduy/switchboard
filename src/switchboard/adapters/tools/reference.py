"""Deterministic local reference tools and their public manifest contracts."""

from dataclasses import dataclass
from datetime import date
from hashlib import sha256

from switchboard.application.ports.tool_adapter import (
    ToolInvocationFailure,
    ToolInvocationRequest,
    ToolInvocationResult,
    ToolInvocationSuccess,
    ToolReconciliationResult,
)
from switchboard.application.services.tool_conformance import ToolConformanceSuite
from switchboard.domain.json_values import JsonObject, canonical_json
from switchboard.domain.tools import (
    JSON_SCHEMA_DRAFT_2020_12,
    TOOL_MANIFEST_SCHEMA_VERSION,
    RetryPolicyCandidate,
    ToolManifestCandidate,
)

TRANSIENT_ERROR_TRIGGER = "__conformance_transient_error__"


@dataclass(frozen=True, slots=True)
class ReferenceWorkItem:
    id: str
    title: str
    status: str
    due_date: str | None


DEFAULT_WORK_ITEMS = (
    ReferenceWorkItem("WI-1", "Prepare launch checklist", "open", "2026-07-20"),
    ReferenceWorkItem("WI-2", "Review access policy", "in_progress", "2026-07-18"),
    ReferenceWorkItem("WI-3", "Archive completed experiments", "done", None),
)


class SearchWorkItemsAdapter:
    """Search a fixed synthetic dataset without external side effects."""

    def __init__(self, work_items: tuple[ReferenceWorkItem, ...] = DEFAULT_WORK_ITEMS) -> None:
        self._work_items = tuple(work_items)

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        query = request.arguments.get("query")
        limit = request.arguments.get("limit", 10)
        if not isinstance(query, str) or not query.strip() or not isinstance(limit, int):
            return ToolInvocationFailure("invalid_input", retryable=False)
        if query == TRANSIENT_ERROR_TRIGGER:
            return ToolInvocationFailure("temporarily_unavailable", retryable=True)
        normalized = query.casefold()
        matches = tuple(
            item
            for item in self._work_items
            if normalized in item.id.casefold() or normalized in item.title.casefold()
        )[:limit]
        return ToolInvocationSuccess(
            {
                "items": [
                    {
                        "id": item.id,
                        "title": item.title,
                        "status": item.status,
                        "due_date": item.due_date,
                    }
                    for item in matches
                ]
            }
        )

    async def reconcile(self, idempotency_key: str) -> ToolReconciliationResult:
        del idempotency_key
        return ToolReconciliationResult(found=False, output=None)


class UpdateDueDateAdapter:
    """Apply deterministic in-memory updates with logical-key idempotency."""

    def __init__(self, work_items: tuple[ReferenceWorkItem, ...] = DEFAULT_WORK_ITEMS) -> None:
        self._due_dates = {item.id: item.due_date for item in work_items}
        self._operations: dict[str, tuple[str, JsonObject]] = {}

    async def invoke(self, request: ToolInvocationRequest) -> ToolInvocationResult:
        work_item_id = request.arguments.get("work_item_id")
        due_date = request.arguments.get("due_date")
        if not isinstance(work_item_id, str) or not isinstance(due_date, str):
            return ToolInvocationFailure("invalid_input", retryable=False)
        if work_item_id == TRANSIENT_ERROR_TRIGGER:
            return ToolInvocationFailure("temporarily_unavailable", retryable=True)
        if request.idempotency_key is None:
            return ToolInvocationFailure("idempotency_required", retryable=False)
        if work_item_id not in self._due_dates:
            return ToolInvocationFailure("work_item_not_found", retryable=False)
        try:
            date.fromisoformat(due_date)
        except ValueError:
            return ToolInvocationFailure("invalid_due_date", retryable=False)

        fingerprint = canonical_json(request.arguments)
        existing = self._operations.get(request.idempotency_key)
        if existing is not None:
            if existing[0] != fingerprint:
                return ToolInvocationFailure("idempotency_key_conflict", retryable=False)
            return ToolInvocationSuccess(existing[1])

        operation_reference = "op-" + sha256(request.idempotency_key.encode()).hexdigest()[:16]
        output: JsonObject = {
            "work_item_id": work_item_id,
            "due_date": due_date,
            "operation_reference": operation_reference,
        }
        self._due_dates[work_item_id] = due_date
        self._operations[request.idempotency_key] = (fingerprint, output)
        return ToolInvocationSuccess(output)

    async def reconcile(self, idempotency_key: str) -> ToolReconciliationResult:
        existing = self._operations.get(idempotency_key)
        return ToolReconciliationResult(
            found=existing is not None,
            output=None if existing is None else existing[1],
        )


def search_work_items_manifest() -> ToolManifestCandidate:
    input_schema = {
        "$schema": JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "query": {"type": "string", "minLength": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        },
        "required": ["query"],
    }
    item_schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "id": {"type": "string"},
            "title": {"type": "string"},
            "status": {"type": "string"},
            "due_date": {"type": ["string", "null"]},
        },
        "required": ["id", "title", "status", "due_date"],
    }
    output_schema = {
        "$schema": JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "additionalProperties": False,
        "properties": {"items": {"type": "array", "items": item_schema}},
        "required": ["items"],
    }
    return ToolManifestCandidate(
        schema_version=TOOL_MANIFEST_SCHEMA_VERSION,
        display_name="Search work items",
        description="Search normalized synthetic work-item summaries.",
        input_schema=input_schema,
        output_schema=output_schema,
        effect="read_only",
        required_scopes=("work_items:read",),
        timeout_ms=1_000,
        retry_policy=RetryPolicyCandidate(2, 10, ("temporarily_unavailable",)),
        idempotency="none",
        reconciliation="none",
        adapter_key="reference.search_work_items.v1",
    )


def update_due_date_manifest() -> ToolManifestCandidate:
    input_schema = {
        "$schema": JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "work_item_id": {"type": "string", "minLength": 1},
            "due_date": {"type": "string", "format": "date"},
        },
        "required": ["work_item_id", "due_date"],
    }
    output_schema = {
        "$schema": JSON_SCHEMA_DRAFT_2020_12,
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "work_item_id": {"type": "string"},
            "due_date": {"type": "string", "format": "date"},
            "operation_reference": {"type": "string", "x-sensitive": True},
        },
        "required": ["work_item_id", "due_date", "operation_reference"],
    }
    return ToolManifestCandidate(
        schema_version=TOOL_MANIFEST_SCHEMA_VERSION,
        display_name="Update due date",
        description="Update one synthetic work item using an idempotent logical operation.",
        input_schema=input_schema,
        output_schema=output_schema,
        effect="mutating",
        required_scopes=("work_items:read", "work_items:write"),
        timeout_ms=1_000,
        retry_policy=RetryPolicyCandidate(2, 10, ("temporarily_unavailable",)),
        idempotency="required",
        reconciliation="by_idempotency_key",
        adapter_key="reference.update_due_date.v1",
        sensitive_output_paths=("/operation_reference",),
    )


def search_work_items_suite() -> ToolConformanceSuite:
    return ToolConformanceSuite(
        valid_input={"query": "launch", "limit": 5},
        invalid_input={"query": ""},
        invalid_output={"items": "not-an-array"},
        timeout_input={"query": "policy"},
        declared_error_input={"query": TRANSIENT_ERROR_TRIGGER},
        expected_error_code="temporarily_unavailable",
        idempotency_input={"query": "launch"},
        reconciliation_input={"query": "launch"},
        sensitive_input={"query": "launch"},
        sensitive_output={"items": []},
    )


def update_due_date_suite() -> ToolConformanceSuite:
    operation_output = {
        "work_item_id": "WI-1",
        "due_date": "2026-08-01",
        "operation_reference": "synthetic-sensitive-reference",
    }
    return ToolConformanceSuite(
        valid_input={"work_item_id": "WI-1", "due_date": "2026-07-25"},
        invalid_input={"work_item_id": "WI-1"},
        invalid_output={"work_item_id": "WI-1"},
        timeout_input={"work_item_id": "WI-2", "due_date": "2026-07-26"},
        declared_error_input={
            "work_item_id": TRANSIENT_ERROR_TRIGGER,
            "due_date": "2026-07-27",
        },
        expected_error_code="temporarily_unavailable",
        idempotency_input={"work_item_id": "WI-1", "due_date": "2026-08-01"},
        reconciliation_input={"work_item_id": "WI-1", "due_date": "2026-08-01"},
        sensitive_input={"work_item_id": "WI-1", "due_date": "2026-08-01"},
        sensitive_output=operation_output,
    )
