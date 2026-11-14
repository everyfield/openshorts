"""Tests for the provider-neutral native publishing contract."""
import pytest
from native_social.state import InvalidPublishTransition, may_retry_automatically, transition
from native_social.types import PublishStatus

def test_happy_path_state_transitions():
    state = transition(PublishStatus.PENDING, PublishStatus.QUEUED)
    state = transition(state, PublishStatus.PUBLISHING)
    state = transition(state, PublishStatus.PROCESSING)
    state = transition(state, PublishStatus.PUBLISHED)
    assert state is PublishStatus.PUBLISHED

def test_ambiguous_publish_must_be_reconciled_not_retried():
    assert not may_retry_automatically(request_was_transmitted=True, provider_confirms_no_operation=False, failure_is_transient=True)

def test_confirmed_no_operation_can_retry_transient_failure():
    assert may_retry_automatically(request_was_transmitted=True, provider_confirms_no_operation=True, failure_is_transient=True)

def test_permanent_failure_is_not_retried():
    assert not may_retry_automatically(request_was_transmitted=False, provider_confirms_no_operation=False, failure_is_transient=False)

def test_published_is_terminal():
    with pytest.raises(InvalidPublishTransition):
        transition(PublishStatus.PUBLISHED, PublishStatus.QUEUED)

def test_publishing_cannot_skip_to_cancelled():
    with pytest.raises(InvalidPublishTransition):
        transition(PublishStatus.PUBLISHING, PublishStatus.CANCELLED)