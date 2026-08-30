"""Authentication, scope resolution and role gating `[AU-*]`, `[AZ-*]`.

Authentication is a **global** dependency with a small public allowlist, not a
per-route decorator. A route added later is therefore protected by default: the
failure mode of the opt-in design is a forgotten decorator on exactly the
endpoint that needed it most, and that failure is silent `[AC-15]`.

Scope is resolved once, here, into a frozen `RequestContext`. Nothing downstream
can widen it, and the model never sees it at all - which is why prompt injection
cannot reach account selection `[AZ-06]`.
"""

import hmac
import time
from collections import defaultdict
from dataclasses import dataclass

from fastapi import Request

from texting_agent.config import ApiKey, Role, settings

API_KEY_HEADER = "X-API-Key"

# Everything else requires a key. Documentation is public because it describes
# the contract, not the data.
PUBLIC_PATHS = frozenset({"/", "/health", "/docs", "/redoc", "/openapi.json"})


class APIError(Exception):
    """An error the caller is allowed to see, in the uniform envelope."""

    def __init__(self, status_code: int, code: str, message: str,
                 details: list[dict] | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or []


@dataclass(frozen=True)
class RequestContext:
    """Resolved once per request and never mutated `[AZ-01]`."""

    key_id: str
    role: Role
    accounts: tuple[str, ...]

    def in_scope(self, account_id: str) -> bool:
        return account_id in self.accounts


def _match_key(presented: str) -> ApiKey | None:
    """Constant-time comparison against every configured key `[AU-04]`.

    No early return: the loop always runs to the end, so neither the secret nor
    the key's position in the list is observable through timing.
    """
    found: ApiKey | None = None
    for key in settings.api_keys:
        if hmac.compare_digest(key.secret, presented):
            found = key
    return found


async def authenticate(request: Request) -> None:
    """Global dependency. Attaches the context; never returns key material."""
    if request.url.path in PUBLIC_PATHS:
        return

    presented = request.headers.get(API_KEY_HEADER, "")
    key = _match_key(presented) if presented else None
    if key is None:
        # Identical response for absent and unknown, with no hint as to which
        # `[AU-02]`.
        raise APIError(401, "UNAUTHORIZED", "A valid API key is required.")

    request.state.context = RequestContext(
        key_id=key.key_id, role=key.role, accounts=tuple(key.accounts)
    )


def get_context(request: Request) -> RequestContext:
    context = getattr(request.state, "context", None)
    if context is None:                        # pragma: no cover - unreachable
        raise APIError(401, "UNAUTHORIZED", "A valid API key is required.")
    return context


def require_role(role: Role):
    """Role gate for a route. Approval and rejection use `Role.APPROVER`."""

    def dependency(request: Request) -> RequestContext:
        context = get_context(request)
        if context.role is not role:
            raise APIError(403, "FORBIDDEN",
                           f"This action requires the {role.value} role.")
        return context

    return dependency


require_operator = require_role(Role.OPERATOR)
require_approver = require_role(Role.APPROVER)


def require_account(context: RequestContext, account_id: str | None) -> str:
    """Validate a body-supplied account against the caller's scope `[FR-63b]`,
    `[FR-66]`. 400 when absent, 403 when out of scope - the distinction matters
    because one is the caller's mistake and the other is a refusal."""
    if not account_id:
        raise APIError(400, "ACCOUNT_REQUIRED", "account_id is required.")
    if not context.in_scope(account_id):
        raise APIError(403, "FORBIDDEN", "That account is not in scope for this key.")
    return account_id


# --- rate limiting ---------------------------------------------------------

# ponytail: in-process fixed windows. Correct for one worker, which is what v1
# runs; a shared store is the upgrade when there is more than one.
_windows: dict[tuple[str, int], int] = defaultdict(int)


def rate_limit(request: Request) -> None:
    """Per-key, per-minute cap on the expensive routes `[SEC-13]`."""
    context = get_context(request)
    minute = int(time.time() // 60)
    _windows_key = (context.key_id, minute)
    _windows[_windows_key] += 1
    for stale in [k for k in _windows if k[1] < minute]:
        del _windows[stale]
    if _windows[_windows_key] > settings.rate_limit_per_minute:
        raise APIError(429, "RATE_LIMITED",
                       "Too many requests. Try again in a minute.")


def reset_rate_limits() -> None:
    """Test hook. Nothing in the request path calls this."""
    _windows.clear()
