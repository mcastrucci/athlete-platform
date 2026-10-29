"""Identity established by a trusted authentication adapter."""

from dataclasses import dataclass


@dataclass(frozen=True)
class VerifiedIdentity:
    issuer: str
    subject: str


class InvalidIdentity(Exception):
    """Credentials could not be verified."""


class IdentityUnavailable(Exception):
    """Authentication is not configured or its verification service is unavailable."""
