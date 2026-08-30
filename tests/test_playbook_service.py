"""Playbook config `[FR-23]`. A bad playbooks.yaml must fail the boot, not widen
what the model is allowed to offer a customer."""

import yaml
import pytest

from texting_agent.config import settings
from texting_agent.schemas.campaign import OfferType, PlaybookId
from texting_agent.schemas.churn import ValueTier
from texting_agent.services import playbook_service


@pytest.fixture
def raw():
    from pathlib import Path
    return yaml.safe_load((Path(settings.config_dir) / "playbooks.yaml").read_text())


def write(tmp_path, raw):
    path = tmp_path / "playbooks.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def test_the_shipped_config_loads():
    config = playbook_service.load()
    assert set(config.playbooks) == set(PlaybookId)


def test_every_tier_has_somewhere_to_go():
    config = playbook_service.load()
    assert all(config.for_tier(tier) for tier in ValueTier)


def test_offer_types_are_bounded_by_the_playbook():
    config = playbook_service.load()
    assert config.allows(PlaybookId.VIP_REACTIVATION, OfferType.LOYALTY_POINTS)
    assert not config.allows(PlaybookId.VIP_REACTIVATION, OfferType.FIXED_DISCOUNT)


def test_an_unknown_playbook_id_is_rejected(tmp_path, raw):
    raw["playbooks"]["FREE_FOR_ALL"] = raw["playbooks"]["DORMANT"]
    with pytest.raises(ValueError):
        playbook_service.load(write(tmp_path, raw))


def test_an_unknown_offer_type_is_rejected(tmp_path, raw):
    """A typo here would silently let the model propose an offer nothing checks."""
    raw["playbooks"]["DORMANT"]["allowed_offer_types"] = ["PERCENTAGE_DISCUONT"]
    with pytest.raises(ValueError):
        playbook_service.load(write(tmp_path, raw))


def test_a_playbook_with_no_offer_types_is_rejected(tmp_path, raw):
    raw["playbooks"]["DORMANT"]["allowed_offer_types"] = []
    with pytest.raises(ValueError):
        playbook_service.load(write(tmp_path, raw))


def test_a_tier_no_playbook_covers_is_rejected(tmp_path, raw):
    """Otherwise a customer can be scored and tiered, then quietly have no
    campaign to belong to."""
    for playbook in raw["playbooks"].values():
        playbook["applies_to_tiers"] = [
            t for t in playbook["applies_to_tiers"] if t != "LOW_VALUE"
        ]
    with pytest.raises(ValueError, match="LOW_VALUE"):
        playbook_service.load(write(tmp_path, raw))
