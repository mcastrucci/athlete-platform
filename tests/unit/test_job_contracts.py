"""Reject invalid queue inputs before database or provider operations."""

import pytest
from pydantic import ValidationError

from athlete_platform.application.jobs import QueueLimits
from athlete_platform.infrastructure.jobs import validated_payload


def test_payload_hash_is_stable_and_inputs_are_bounded() -> None:
    assert validated_payload({"a": 1, "b": 2})[1] == validated_payload({"b": 2, "a": 1})[1]
    with pytest.raises(ValueError):
        validated_payload({"bad": float("nan")})
    with pytest.raises(ValueError, match="64 KiB"):
        validated_payload({"large": "a" * 65536})


def test_queue_limits_are_positive_and_bounded() -> None:
    with pytest.raises(ValidationError):
        QueueLimits(lease_seconds=0)
    with pytest.raises(ValidationError):
        QueueLimits(max_attempts=100)
