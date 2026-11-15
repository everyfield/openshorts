"""Publication target state machine and retry classification helpers."""
from __future__ import annotations
from .types import PublishStatus

_ALLOWED: dict[PublishStatus, frozenset[PublishStatus]] = {
    PublishStatus.PENDING: frozenset({PublishStatus.QUEUED, PublishStatus.CANCELLED}),
    PublishStatus.QUEUED: frozenset({PublishStatus.PUBLISHING, PublishStatus.CANCELLED}),
    PublishStatus.PUBLISHING: frozenset({PublishStatus.PROCESSING, PublishStatus.PUBLISHED, PublishStatus.FAILED, PublishStatus.NEEDS_REVIEW}),
    PublishStatus.PROCESSING: frozenset({PublishStatus.PUBLISHED, PublishStatus.FAILED, PublishStatus.NEEDS_REVIEW}),
    PublishStatus.PUBLISHED: frozenset(),
    PublishStatus.FAILED: frozenset({PublishStatus.QUEUED}),
    PublishStatus.NEEDS_REVIEW: frozenset({PublishStatus.PUBLISHED, PublishStatus.FAILED}),
    PublishStatus.CANCELLED: frozenset(),
}

class InvalidPublishTransition(ValueError):
    """Raised when a target attempts an illegal state transition."""

def transition(current: PublishStatus, target: PublishStatus) -> PublishStatus:
    """Validate and return a target state; callers must persist atomically."""
    if target not in _ALLOWED[current]:
        raise InvalidPublishTransition(f"cannot transition {current.value} -> {target.value}")
    return target

def may_retry_automatically(*, request_was_transmitted: bool, provider_confirms_no_operation: bool, failure_is_transient: bool) -> bool:
    """Retry only when no provider-side operation could have been created."""
    return failure_is_transient and (not request_was_transmitted or provider_confirms_no_operation)