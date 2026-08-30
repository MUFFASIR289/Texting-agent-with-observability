"""Dev-only engagement events `[FR-55]`, `[FR-63c]`, `[RV-D3]`.

Without this the demo loop breaks between "send the campaign" and "how did it
perform", and a human would have to run a script in the middle of it.

Rates are per event type and the seed is fixed, so analytics is exercised
against numbers that do not move between runs `[NFR-10]`. An UNSUBSCRIBED or
BOUNCED event writes its suppression through the repository, in the same
transaction, so the simulated world obeys the same rules as the real one.
"""

import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from texting_agent.database.repositories.campaign_repo import CampaignRepository

SEED = 20260830

# Funnel rates, each conditional on the step before it. Deliberately plausible
# rather than flattering: a demo that shows a 90% conversion rate teaches the
# operator to distrust the numbers.
DEFAULT_RATES = {
    "DELIVERED": 0.96,
    "OPENED": 0.42,        # of delivered
    "CLICKED": 0.28,       # of opened
    "CONVERTED": 0.18,     # of clicked
    "UNSUBSCRIBED": 0.02,  # of delivered
    "BOUNCED": 0.03,       # of attempted
}

REVENUE_RANGE = (25.0, 320.0)


@dataclass
class SimulationReport:
    campaign_id: str
    events: dict[str, int]
    revenue: float

    @property
    def total(self) -> int:
        return sum(self.events.values())


def simulate(repo: CampaignRepository, campaign_id: str,
             rates: dict[str, float] | None = None,
             seed: int = SEED) -> SimulationReport:
    """Generate one funnel per SENT row. Skipped and failed sends produce
    nothing, because nothing reached anybody."""
    rng = random.Random(f"{seed}|{campaign_id}")
    rates = {**DEFAULT_RATES, **(rates or {})}
    counts: dict[str, int] = {}
    revenue = 0.0
    now = datetime.now(UTC)

    def record(send_id: str, event_type: str, minutes: int,
               amount: float | None = None) -> None:
        nonlocal revenue
        repo.record_event(send_id, event_type, revenue=amount,
                          occurred_at=(now + timedelta(minutes=minutes)).isoformat())
        counts[event_type] = counts.get(event_type, 0) + 1
        if amount:
            revenue += amount

    for row in repo.list_sends(campaign_id):
        if row["status"] != "SENT":
            continue
        send_id = row["send_id"]

        if rng.random() < rates["BOUNCED"]:
            record(send_id, "BOUNCED", 1)      # also writes a suppression
            continue
        if rng.random() >= rates["DELIVERED"]:
            continue
        record(send_id, "DELIVERED", 2)

        if rng.random() < rates["UNSUBSCRIBED"]:
            record(send_id, "UNSUBSCRIBED", 30)
            continue
        if rng.random() >= rates["OPENED"]:
            continue
        record(send_id, "OPENED", 45)

        if rng.random() >= rates["CLICKED"]:
            continue
        record(send_id, "CLICKED", 60)

        if rng.random() < rates["CONVERTED"]:
            record(send_id, "CONVERTED", 240,
                   amount=round(rng.uniform(*REVENUE_RANGE), 2))

    return SimulationReport(campaign_id=campaign_id, events=counts,
                            revenue=round(revenue, 2))
