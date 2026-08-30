"""Delivery providers `[FR-49]`, `[FR-50]`.

Protocols, plus mocks that log instead of sending. Two properties matter more
than realism:

* **The agent cannot reach them.** These live in `integrations/`, which the
  import-boundary test forbids `agent/**` from importing `[SEC-09]`.
* **Failures are reproducible.** The mock fails on a hash of the recipient, not
  on a random draw, so a run that exercises the retry path exercises it the same
  way twice `[NFR-10]`.
"""

import hashlib
from dataclasses import dataclass
from typing import Protocol

import structlog

log = structlog.get_logger()


@dataclass(frozen=True)
class ProviderResult:
    ok: bool
    message_id: str | None = None
    error: str | None = None


class ProviderError(Exception):
    """A transient delivery failure. Retried per policy, and recorded as FAILED
    if the retries run out - never as SENT `[FR-52]`, `[EH-07]`."""


class EmailProvider(Protocol):
    def send(self, to: str, subject: str, body: str) -> ProviderResult: ...


class SMSProvider(Protocol):
    def send(self, to: str, body: str) -> ProviderResult: ...


def _deterministic_failure(recipient: str, failure_rate: float) -> bool:
    """Fail the same recipients every run, at roughly the configured rate.

    `random()` would make a red test unreproducible, which is the one thing a
    failure-path test cannot afford.
    """
    if failure_rate <= 0:
        return False
    digest = hashlib.sha256(recipient.encode("utf-8")).digest()
    return (int.from_bytes(digest[:4], "big") % 1000) < failure_rate * 1000


def _message_id(prefix: str, recipient: str, body: str) -> str:
    digest = hashlib.sha256(f"{recipient}|{body}".encode()).hexdigest()
    return f"{prefix}_{digest[:24]}"


@dataclass
class MockEmailProvider:
    failure_rate: float = 0.0

    def send(self, to: str, subject: str, body: str) -> ProviderResult:
        if _deterministic_failure(to, self.failure_rate):
            raise ProviderError("mock provider: temporary delivery failure")
        # The recipient is not logged: an address in a log line is an address in
        # every sink that log is shipped to `[SEC-07]`.
        log.info("provider.email.sent", subject_length=len(subject),
                 body_length=len(body))
        return ProviderResult(ok=True, message_id=_message_id("eml", to, body))


@dataclass
class MockSMSProvider:
    failure_rate: float = 0.0

    def send(self, to: str, body: str) -> ProviderResult:
        if _deterministic_failure(to, self.failure_rate):
            raise ProviderError("mock provider: temporary delivery failure")
        log.info("provider.sms.sent", body_length=len(body))
        return ProviderResult(ok=True, message_id=_message_id("sms", to, body))


class CircuitBreaker:
    """Opens after N consecutive failures `[EH-12]`.

    A provider that has failed five times in a row is down, and continuing to
    hammer it turns one outage into a much longer one. Any success resets the
    count: the breaker is about a run of failures, not a total.
    """

    def __init__(self, threshold: int) -> None:
        self._threshold = threshold
        self._consecutive = 0

    @property
    def is_open(self) -> bool:
        return self._consecutive >= self._threshold

    def record(self, *, ok: bool) -> None:
        self._consecutive = 0 if ok else self._consecutive + 1


def build_email_provider(failure_rate: float = 0.0) -> EmailProvider:
    return MockEmailProvider(failure_rate=failure_rate)


def build_sms_provider(failure_rate: float = 0.0) -> SMSProvider:
    return MockSMSProvider(failure_rate=failure_rate)
