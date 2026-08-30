"""The state machine `[FR-63]`, `[EC-11]`, `[EC-12]`, `[EH-09]`."""

import pytest

from texting_agent.database import app_db
from texting_agent.database.repositories.campaign_repo import CampaignRepository
from texting_agent.orchestrator.transitions import (
    ALLOWED,
    TERMINAL,
    InvalidTransition,
    is_allowed,
    transition,
)
from texting_agent.schemas.campaign import CampaignState as S


@pytest.fixture
def repo(tmp_path):
    path = tmp_path / "app.db"
    app_db.bootstrap(path)
    return CampaignRepository(app_db.connect(path))


@pytest.fixture
def campaign(repo) -> str:
    return repo.create_campaign(account_id="ACC_A", goal="win back lapsed buyers",
                                created_by="ops-1", prompt_version="v1",
                                config_version="1")


def drive(repo, campaign_id: str, path: list[S]) -> None:
    current = S(repo.current_state(campaign_id))
    for nxt in path:
        transition(repo, campaign_id, current, nxt)
        current = nxt


# --- the graph -------------------------------------------------------------


def test_there_are_thirteen_states():
    assert len(ALLOWED) == 13
    assert len(S) == 13


def test_terminal_states_have_no_way_out():
    assert TERMINAL == {S.SENT, S.REJECTED, S.FAILED, S.CANCELLED}
    for state in TERMINAL:
        assert ALLOWED[state] == set()


def test_every_non_terminal_state_can_fail_or_be_cancelled():
    for state, onward in ALLOWED.items():
        if state in TERMINAL:
            continue
        assert {S.FAILED, S.CANCELLED} <= onward, state


def test_only_awaiting_approval_can_be_rejected():
    rejectable = [state for state, onward in ALLOWED.items() if S.REJECTED in onward]
    assert rejectable == [S.AWAITING_APPROVAL]


def test_the_happy_path_runs_end_to_end(repo, campaign):
    drive(repo, campaign, [S.ANALYZING, S.SEGMENTED, S.PLANNED, S.CONTENT_READY,
                           S.VALIDATED, S.AWAITING_APPROVAL, S.APPROVED,
                           S.SENDING, S.SENT])
    assert repo.current_state(campaign) == "SENT"


# --- refusals --------------------------------------------------------------


def test_skipping_a_state_is_refused(repo, campaign):
    """A campaign cannot jump from RECEIVED to APPROVED, however it is asked."""
    with pytest.raises(InvalidTransition):
        transition(repo, campaign, S.RECEIVED, S.APPROVED)
    assert repo.current_state(campaign) == "RECEIVED"


def test_a_terminal_campaign_cannot_be_moved(repo, campaign):
    drive(repo, campaign, [S.CANCELLED])
    with pytest.raises(InvalidTransition):
        transition(repo, campaign, S.CANCELLED, S.ANALYZING)


def test_approval_after_cancellation_loses(repo, campaign):
    """EC-11: cancellation wins, and the approver is told why rather than
    silently succeeding against a dead campaign."""
    drive(repo, campaign, [S.ANALYZING, S.SEGMENTED, S.PLANNED, S.CONTENT_READY,
                           S.VALIDATED, S.AWAITING_APPROVAL])
    transition(repo, campaign, S.AWAITING_APPROVAL, S.CANCELLED)
    with pytest.raises(InvalidTransition) as raised:
        transition(repo, campaign, S.AWAITING_APPROVAL, S.APPROVED)
    assert raised.value.current == "CANCELLED"
    assert raised.value.requested is S.APPROVED


def test_a_missing_campaign_is_an_invalid_transition_not_a_crash(repo):
    with pytest.raises(InvalidTransition) as raised:
        transition(repo, "no-such-campaign", S.RECEIVED, S.ANALYZING)
    assert raised.value.current == "missing"


# --- the race --------------------------------------------------------------


def test_only_one_of_two_concurrent_approvals_wins(repo, campaign):
    """EC-12. Under a read-then-write both approvals would read
    AWAITING_APPROVAL and both would write APPROVED, and the campaign would be
    approved twice by two different people."""
    drive(repo, campaign, [S.ANALYZING, S.SEGMENTED, S.PLANNED, S.CONTENT_READY,
                           S.VALIDATED, S.AWAITING_APPROVAL])
    first = repo.try_transition(campaign, S.AWAITING_APPROVAL, S.APPROVED)
    second = repo.try_transition(campaign, S.AWAITING_APPROVAL, S.APPROVED)
    assert [first, second] == [True, False]
    assert repo.current_state(campaign) == "APPROVED"


def test_a_lost_race_leaves_no_side_effect(repo, campaign):
    drive(repo, campaign, [S.ANALYZING])
    before = dict(repo.get("ACC_A", campaign))
    with pytest.raises(InvalidTransition):
        transition(repo, campaign, S.RECEIVED, S.ANALYZING)   # stale expectation
    assert dict(repo.get("ACC_A", campaign)) == before


def test_is_allowed_matches_what_transition_does(repo, campaign):
    """The table and the behaviour cannot drift apart."""
    for target in S:
        if is_allowed(S.RECEIVED, target):
            continue
        with pytest.raises(InvalidTransition):
            transition(repo, campaign, S.RECEIVED, target)
