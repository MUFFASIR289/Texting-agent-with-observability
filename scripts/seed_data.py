"""Generate synthetic customer data: 3 accounts x 5,000 customers.

Reproducibility: archetype assignment and every random draw come from a fixed
seed, so re-running produces the same customers with the same *relative* history
("47 days inactive" stays 47 days inactive). Absolute timestamps are anchored on
the run date, so a database seeded today is not stale tomorrow.

Every edge case named in SRS section 10 is planted on purpose, because an edge
case that only appears by luck is one the test suite cannot rely on.

    uv run python scripts/seed_data.py
"""

import argparse
import random
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from texting_agent.config import settings
from texting_agent.database import agent_db

SEED = 20260829

# Account profiles. ACC_C is an account where almost nobody has purchased (EC-23).
# ACC_D is deliberately tiny so that too few purchasers exist for percentile
# tiering to mean anything (EC-24) - an edge case that needs a small account, not
# a small share of a large one.
ACCOUNTS = [
    ("ACC_A", "Northwind Retail", 5_000, "balanced"),
    ("ACC_B", "Lumen Wellness", 5_000, "sms_primary"),
    ("ACC_C", "Atlas Marketplace", 5_000, "mostly_browsers"),
    ("ACC_D", "Fen Studio", 12, "tiny"),
]

# archetype -> weight, per profile
MIX = {
    "balanced": {
        "healthy": 34, "engagement_decline": 16, "purchase_decline": 12,
        "dormant": 14, "cart_abandoner": 9, "support_friction": 5,
        "never_purchased": 5, "brand_new": 2, "no_engagement_data": 2,
        "no_current_engagement": 1, "ghost": 1,
    },
    "sms_primary": {
        "healthy": 32, "engagement_decline": 18, "purchase_decline": 11,
        "dormant": 13, "cart_abandoner": 8, "support_friction": 6,
        "never_purchased": 6, "brand_new": 2, "no_engagement_data": 3,
        "no_current_engagement": 1, "ghost": 1,
    },
    # A handful of purchasers, not zero: percentile tiering has to actually run
    # on the tiny sample for EC-24 to be tested rather than short-circuited.
    "tiny": {
        "never_purchased": 6, "healthy": 3, "dormant": 2, "brand_new": 1,
    },
    "mostly_browsers": {
        "never_purchased": 88, "brand_new": 4, "no_engagement_data": 4,
        "dormant": 2, "healthy": 1, "engagement_decline": 1, "ghost": 1,
    },
}

CATEGORIES = ["apparel", "home", "electronics", "beauty", "outdoor", "grocery"]
FIRST = ["Priya", "Arjun", "Mei", "Tomas", "Sofia", "Kenji", "Amara", "Luca",
         "Noor", "Elena", "Ravi", "Yuki", "Diego", "Fatima", "Ivan", "Chloe"]
LAST = ["Sharma", "Okafor", "Tanaka", "Novak", "Rossi", "Haddad", "Silva",
        "Andersen", "Kimura", "Duarte", "Farouk", "Petrov", "Nakamura", "Costa"]


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _make_customer(rng: random.Random, account_id: str, index: int,
                   archetype: str, profile: str, now: datetime) -> dict:
    """Build one internally consistent record.

    Consistency matters more than realism here: total_spend must agree with
    orders x AOV, windowed order counts must not exceed the lifetime total, and
    last_purchase_at must be present if and only if the customer has ordered.
    Scoring is only meaningful on data that holds together.
    """
    cid = f"{account_id[-1]}{index:05d}"
    name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
    handle = f"{name.split()[0].lower()}.{cid.lower()}@example.test"
    phone = f"+1555{rng.randint(1000000, 9999999)}"

    sms_leaning = profile == "sms_primary"
    tenure_days = rng.randint(120, 1500)
    registration = now - timedelta(days=tenure_days)

    # defaults, overridden per archetype
    orders = 0
    aov = None
    spend = 0.0
    freq = None
    last_purchase = None
    last_activity = now - timedelta(days=rng.randint(0, 20))
    last_login = last_activity
    open_now = round(rng.uniform(0.18, 0.42), 3)
    open_prev = round(open_now * rng.uniform(0.9, 1.15), 3)
    sms_now = round(rng.uniform(0.05, 0.22), 3)
    sms_prev = round(sms_now * rng.uniform(0.9, 1.15), 3)
    orders_90 = 0
    orders_prev = 0
    carts = rng.randint(0, 1)
    support = 0
    status = "ACTIVE"
    data_as_of = now

    def purchase_history(low: int, high: int) -> None:
        nonlocal orders, aov, spend, freq
        orders = rng.randint(low, high)
        aov = round(rng.uniform(24.0, 210.0), 2)
        spend = round(orders * aov * rng.uniform(0.92, 1.08), 2)
        freq = round(tenure_days / max(orders, 1), 1)

    if archetype == "healthy":
        purchase_history(4, 40)
        last_purchase = now - timedelta(days=rng.randint(2, max(3, int(freq * 0.7))))
        orders_90 = min(orders, rng.randint(1, 4))
        orders_prev = min(orders - orders_90, rng.randint(1, 4))

    elif archetype == "engagement_decline":
        purchase_history(3, 30)
        last_purchase = now - timedelta(days=rng.randint(20, 70))
        orders_90 = min(orders, rng.randint(0, 1))
        orders_prev = min(orders - orders_90, rng.randint(1, 3))
        last_activity = now - timedelta(days=rng.randint(18, 55))
        last_login = last_activity - timedelta(days=rng.randint(0, 10))
        open_prev = round(rng.uniform(0.28, 0.48), 3)     # was engaged...
        open_now = round(open_prev * rng.uniform(0.10, 0.35), 3)   # ...now is not
        sms_prev = round(rng.uniform(0.12, 0.26), 3)
        sms_now = round(sms_prev * rng.uniform(0.10, 0.40), 3)

    elif archetype == "purchase_decline":
        purchase_history(6, 45)
        last_purchase = now - timedelta(days=rng.randint(55, 130))
        orders_90 = 0
        orders_prev = min(orders, rng.randint(2, 6))
        last_activity = now - timedelta(days=rng.randint(10, 40))
        last_login = last_activity

    elif archetype == "dormant":
        purchase_history(1, 18)
        last_purchase = now - timedelta(days=rng.randint(150, 600))
        last_activity = now - timedelta(days=rng.randint(95, 400))
        last_login = last_activity
        open_prev = round(rng.uniform(0.10, 0.30), 3)
        open_now = round(rng.uniform(0.0, 0.05), 3)
        sms_now = round(rng.uniform(0.0, 0.04), 3)
        status = "INACTIVE"

    elif archetype == "cart_abandoner":
        purchase_history(2, 20)
        last_purchase = now - timedelta(days=rng.randint(30, 95))
        orders_90 = min(orders, rng.randint(0, 1))
        orders_prev = min(orders - orders_90, rng.randint(1, 3))
        carts = rng.randint(3, 9)
        last_activity = now - timedelta(days=rng.randint(1, 12))
        last_login = last_activity

    elif archetype == "support_friction":
        purchase_history(3, 25)
        last_purchase = now - timedelta(days=rng.randint(25, 90))
        orders_90 = min(orders, rng.randint(0, 2))
        orders_prev = min(orders - orders_90, rng.randint(1, 3))
        support = rng.randint(2, 6)
        open_prev = round(rng.uniform(0.20, 0.40), 3)
        open_now = round(open_prev * rng.uniform(0.3, 0.7), 3)

    elif archetype == "never_purchased":          # EC-03
        last_activity = now - timedelta(days=rng.randint(0, 120))
        last_login = last_activity
        carts = rng.randint(0, 4)

    elif archetype == "brand_new":                # EC-04
        registration = now - timedelta(days=rng.randint(0, 2))
        last_activity = now - timedelta(hours=rng.randint(0, 30))
        last_login = last_activity
        open_prev = None                          # no prior window exists yet
        sms_prev = None

    elif archetype == "no_engagement_data":       # EC-05
        purchase_history(1, 12)
        last_purchase = now - timedelta(days=rng.randint(40, 200))
        open_now = open_prev = sms_now = sms_prev = None
        last_activity = now - timedelta(days=rng.randint(30, 120))
        last_login = last_activity

    elif archetype == "ghost":                    # FR-04c
        # A registration and nothing since: no activity, no login, no purchase, no
        # engagement. Too little to rank, so scoring must return UNKNOWN rather
        # than inventing a number from two zero counters.
        last_activity = None
        last_login = None
        open_now = open_prev = sms_now = sms_prev = None
        carts = 0

    elif archetype == "no_current_engagement":    # EC-25
        purchase_history(2, 15)
        last_purchase = now - timedelta(days=rng.randint(40, 120))
        open_now = None
        sms_now = None
        open_prev = round(rng.uniform(0.20, 0.40), 3)
        sms_prev = round(rng.uniform(0.10, 0.25), 3)

    # A handful of deliberately stale rows, to exercise FR-10a exclusion.
    if rng.random() < 0.02:
        data_as_of = now - timedelta(days=rng.randint(8, 30))

    click_now = round(open_now * rng.uniform(0.15, 0.55), 3) if open_now else open_now
    channel = "SMS" if sms_leaning and rng.random() < 0.55 else "EMAIL"
    if archetype == "no_engagement_data":
        channel = None

    return {
        "account_id": account_id,
        "customer_id": cid,
        "customer_name": name,
        "email": handle,
        "phone": phone,
        "customer_status": status,
        "registration_date": _iso(registration),
        "last_activity_at": _iso(last_activity),
        "last_login_at": _iso(last_login),
        "last_purchase_at": _iso(last_purchase),
        "total_orders": orders,
        "total_spend": spend,
        "average_order_value": aov,
        "purchase_frequency_days": freq,
        "email_open_rate": open_now,
        "email_click_rate": click_now,
        "sms_response_rate": sms_now,
        "orders_last_90d": orders_90,
        "cart_abandonment_count_90d": carts,
        "support_issue_count_90d": support,
        "email_open_rate_prev_90d": open_prev,
        "sms_response_rate_prev_90d": sms_prev,
        "orders_prev_90d": orders_prev,
        "preferred_channel": channel,
        "email_consent": int(rng.random() < 0.94),
        "sms_consent": int(rng.random() < 0.71),
        "last_purchase_category": rng.choice(CATEGORIES) if orders else None,
        "data_as_of": _iso(data_as_of),
    }


def generate(now: datetime | None = None) -> list[dict]:
    rng = random.Random(SEED)
    now = now or datetime.now(UTC)
    rows: list[dict] = []
    for account_id, _label, size, profile in ACCOUNTS:
        mix = MIX[profile]
        archetypes = list(mix)
        weights = [mix[a] for a in archetypes]
        for i in range(1, size + 1):
            archetype = rng.choices(archetypes, weights=weights, k=1)[0]
            rows.append(_make_customer(rng, account_id, i, archetype, profile, now))
    return rows


def seed(path: Path, rows: list[dict]) -> None:
    if path.exists():
        path.unlink()
    agent_db.create(path)
    columns = list(rows[0])
    placeholders = ", ".join(":" + c for c in columns)
    statement = (
        "INSERT INTO customer_agent_records ("
        + ", ".join(columns)
        + ") VALUES ("
        + placeholders
        + ")"
    )
    with sqlite3.connect(path) as conn:
        conn.executemany(statement, rows)
        conn.commit()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed the agent database.")
    parser.add_argument("--path", default=settings.agent_db_path)
    args = parser.parse_args()

    path = Path(args.path)
    rows = generate()
    seed(path, rows)

    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        summary = conn.execute(
            "SELECT account_id, COUNT(*) AS total, "
            "SUM(CASE WHEN total_orders > 0 THEN 1 ELSE 0 END) AS purchasers "
            "FROM customer_agent_records GROUP BY account_id ORDER BY account_id"
        ).fetchall()

    print(f"seeded {len(rows)} customers -> {path}")
    for row in summary:
        print(f"  {row['account_id']}: {row['total']:>5} customers, "
              f"{row['purchasers']:>4} purchasers")


if __name__ == "__main__":
    main()
