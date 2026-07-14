from switchboard.adapters.schema.jsonschema_validator import Draft202012JsonSchemaValidator
from switchboard.adapters.tools.reference import (
    SearchWorkItemsAdapter,
    UpdateDueDateAdapter,
    search_work_items_manifest,
    update_due_date_manifest,
)
from switchboard.adapters.tools.resolver import StaticToolAdapterResolver
from switchboard.application.ports.tool_adapter import (
    ToolInvocationFailure,
    ToolInvocationRequest,
    ToolInvocationSuccess,
)
from switchboard.application.services.tool_manifest_validation import ToolManifestValidator


def test_reference_manifests_validate_as_safe_contracts() -> None:
    validator = ToolManifestValidator(Draft202012JsonSchemaValidator())

    search = validator.validate(search_work_items_manifest())
    update = validator.validate(update_due_date_manifest())

    assert search.is_valid
    assert update.is_valid
    assert search.manifest is not None
    assert update.manifest is not None
    assert search.manifest.adapter_key == "reference.search_work_items.v1"
    assert update.manifest.sensitive_output_paths == ("/operation_reference",)


async def test_search_work_items_returns_bounded_normalized_matches() -> None:
    adapter = SearchWorkItemsAdapter()

    result = await adapter.invoke(
        ToolInvocationRequest(arguments={"query": "launch", "limit": 1}, idempotency_key=None)
    )

    assert isinstance(result, ToolInvocationSuccess)
    assert result.output == {
        "items": (
            {
                "id": "WI-1",
                "title": "Prepare launch checklist",
                "status": "open",
                "due_date": "2026-07-20",
            },
        )
    }


async def test_update_due_date_is_idempotent_and_reconcilable() -> None:
    adapter = UpdateDueDateAdapter()
    request = ToolInvocationRequest(
        arguments={"work_item_id": "WI-1", "due_date": "2026-08-01"},
        idempotency_key="operation-1",
    )

    first = await adapter.invoke(request)
    repeated = await adapter.invoke(request)
    reconciled = await adapter.reconcile("operation-1")
    conflict = await adapter.invoke(
        ToolInvocationRequest(
            arguments={"work_item_id": "WI-1", "due_date": "2026-08-02"},
            idempotency_key="operation-1",
        )
    )

    assert isinstance(first, ToolInvocationSuccess)
    assert repeated == first
    assert reconciled.found
    assert reconciled.output == first.output
    assert isinstance(conflict, ToolInvocationFailure)
    assert conflict.error_code == "idempotency_key_conflict"


async def test_update_due_date_requires_an_idempotency_key() -> None:
    result = await UpdateDueDateAdapter().invoke(
        ToolInvocationRequest(
            arguments={"work_item_id": "WI-1", "due_date": "2026-08-01"},
            idempotency_key=None,
        )
    )

    assert isinstance(result, ToolInvocationFailure)
    assert result.error_code == "idempotency_required"


def test_static_resolver_defensively_copies_startup_mapping() -> None:
    search = SearchWorkItemsAdapter()
    adapters = {"reference.search_work_items.v1": search}
    resolver = StaticToolAdapterResolver(adapters)

    adapters.clear()

    assert resolver.resolve("reference.search_work_items.v1") is search
    assert resolver.resolve("missing.adapter") is None
