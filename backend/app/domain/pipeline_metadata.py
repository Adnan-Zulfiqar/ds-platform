"""Which `ProductVersion.content` keys mark a Stage 7 pipeline candidate.

Lives in the domain layer so both the service (activation guard) and the
response schema (history flag, review finding I-2) can ask the same question
without the schema importing a service.
"""

from __future__ import annotations

from typing import Final

PIPELINE_METADATA_KEYS: Final[tuple[str, ...]] = (
    "pipelineCandidateVersion",
    "pipelineSourceUpdatedAt",
    "isSynthetic",
)


def content_has_any_pipeline_metadata_key(content: object) -> bool:
    """True when any pipeline key is present, even if the values are garbage.

    Presence, not validity: a corrupt pipeline row must still be treated as a
    pipeline row, so it can never be activated as if it were a legacy
    optimize version (`True == 1` in Python). Type checking belongs in
    `parse_pipeline_candidate_metadata`.
    """
    if not isinstance(content, dict):
        return False
    return any(key in content for key in PIPELINE_METADATA_KEYS)


__all__ = ["PIPELINE_METADATA_KEYS", "content_has_any_pipeline_metadata_key"]
