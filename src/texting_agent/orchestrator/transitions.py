"""The legal state graph and the only way to move through it `[FR-63]`, `[EH-09]`.

The transition is a conditional UPDATE (`WHERE state = :from`). That is not
belt-and-braces: two approvals arriving at once would both read
`AWAITING_APPROVAL` and both write `APPROVED` under a read-then-write, and the
campaign would be approved twice by two different people `[EC-12]`. Whoever
loses the race gets a 409 and no side effect.
"""

from texting_agent.database.repositories.campaign_repo import CampaignRepository
from texting_agent.schemas.campaign import CampaignState as S

# Terminal off-ramps available from every non-terminal state.
_OFF_RAMPS = {S.FAILED, S.CANCELLED}

ALLOWED: dict[S, set[S]] = {
    S.RECEIVED: {S.ANALYZING} | _OFF_RAMPS,
    S.ANALYZING: {S.SEGMENTED} | _OFF_RAMPS,
    S.SEGMENTED: {S.PLANNED} | _OFF_RAMPS,
    S.PLANNED: {S.CONTENT_READY} | _OFF_RAMPS,
    S.CONTENT_READY: {S.VALIDATED} | _OFF_RAMPS,
    S.VALIDATED: {S.AWAITING_APPROVAL} | _OFF_RAMPS,
    S.AWAITING_APPROVAL: {S.APPROVED, S.REJECTED} | _OFF_RAMPS,
    S.APPROVED: {S.SENDING} | _OFF_RAMPS,
    S.SENDING: {S.SENT} | _OFF_RAMPS,
    # Terminal.
    S.SENT: set(),
    S.REJECTED: set(),
    S.FAILED: set(),
    S.CANCELLED: set(),
}

TERMINAL = frozenset(state for state, onward in ALLOWED.items() if not onward)


class InvalidTransition(Exception):
    """Surfaces as 409 with both states and no side effect `[EH-09]`."""

    def __init__(self, campaign_id: str, current: S | str, requested: S) -> None:
        super().__init__(
            f"campaign {campaign_id} cannot move from {current} to {requested}"
        )
        self.campaign_id = campaign_id
        self.current = current
        self.requested = requested


def is_allowed(current: S, requested: S) -> bool:
    return requested in ALLOWED[current]


def transition(repo: CampaignRepository, campaign_id: str,
               expected: S, requested: S) -> None:
    """Move a campaign, or raise. Never both.

    `expected` goes into the WHERE clause rather than being checked first, so
    the check and the write are one atomic operation and the loser of a race
    learns it lost.
    """
    if not is_allowed(expected, requested):
        raise InvalidTransition(campaign_id, expected, requested)
    if not repo.try_transition(campaign_id, expected, requested):
        raise InvalidTransition(campaign_id, repo.current_state(campaign_id) or "missing",
                                requested)
