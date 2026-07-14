import pytest

from switchboard.application.services.response_chunking import (
    chunk_response_text,
)
from switchboard.domain.errors import DomainValidationError


def test_chunk_response_preserves_reconstructable_text() -> None:
    chunks = chunk_response_text("Project  Alpha\nis overdue.")

    assert chunks == (
        "Project  ",
        "Alpha\n",
        "is ",
        "overdue.",
    )
    assert "".join(chunks) == ("Project  Alpha\nis overdue.")


def test_chunk_response_trims_outer_whitespace() -> None:
    chunks = chunk_response_text("  Project Alpha.  ")

    assert chunks == (
        "Project ",
        "Alpha.",
    )


def test_chunk_response_rejects_blank_text() -> None:
    with pytest.raises(
        DomainValidationError,
        match="response_text must not be blank",
    ):
        chunk_response_text("   ")
