"""Loads and validates `config/playbooks.yaml` `[FR-23]`.

Playbooks bound what the model may propose: an offer type outside the chosen
playbook is rejected downstream, so a typo here would silently widen that bound.
The loader validates every id, tier and offer type against its enum at startup,
where a bad config is a failed boot rather than a bad campaign.
"""

import functools
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from texting_agent.config import settings
from texting_agent.schemas.campaign import OfferType, PlaybookId
from texting_agent.schemas.churn import ValueTier


class Playbook(BaseModel):
    applies_to_tiers: list[ValueTier] = Field(min_length=1)
    allowed_offer_types: list[OfferType] = Field(min_length=1)
    tone: str
    guidance: str


class PlaybookConfig(BaseModel):
    version: int
    playbooks: dict[PlaybookId, Playbook]

    def for_tier(self, tier: ValueTier) -> list[PlaybookId]:
        return [pid for pid, pb in self.playbooks.items() if tier in pb.applies_to_tiers]

    def allows(self, playbook_id: PlaybookId, offer: OfferType) -> bool:
        return offer in self.playbooks[playbook_id].allowed_offer_types


def load(path: Path | str | None = None) -> PlaybookConfig:
    path = path or Path(settings.config_dir) / "playbooks.yaml"
    config = PlaybookConfig.model_validate(
        yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    )
    # Every tier must have somewhere to go, or a customer in it can be scored,
    # segmented, and then silently have no campaign to belong to.
    orphaned = [t for t in ValueTier if not config.for_tier(t)]
    if orphaned:
        raise ValueError(f"no playbook applies to tiers: {[t.value for t in orphaned]}")
    return config


@functools.cache
def get() -> PlaybookConfig:
    return load()
