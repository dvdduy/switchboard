"""Deterministic provider-independent response chunking."""

import re

from switchboard.domain.common import require_not_blank

_CHUNK_PATTERN = re.compile(r"\S+\s*")


def chunk_response_text(
    response_text: str,
) -> tuple[str, ...]:
    """Split text into stable chunks while preserving inner whitespace."""

    normalized = require_not_blank(
        response_text,
        field_name="response_text",
    )

    chunks = tuple(match.group(0) for match in _CHUNK_PATTERN.finditer(normalized))

    if not chunks:
        raise AssertionError("validated response text must produce a chunk")

    return chunks
