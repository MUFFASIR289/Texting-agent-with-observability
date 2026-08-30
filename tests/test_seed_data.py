"""FR-03: the seed is reproducible and plants every edge case on purpose."""

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "seed_data.py"
ANCHOR = datetime(2026, 1, 1, tzinfo=UTC)


def _load():
    spec = importlib.util.spec_from_file_location("seed_data", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rows():
    return _load().generate(ANCHOR)


def test_two_runs_produce_identical_data():
    module = _load()
    assert module.generate(ANCHOR) == module.generate(ANCHOR)


def test_every_documented_edge_case_is_present(rows):
    counts = {
        "EC-03 never purchased": sum(1 for r in rows if r["total_orders"] == 0),
        "EC-05 no engagement data": sum(
            1 for r in rows if r["email_open_rate"] is None and r["sms_response_rate"] is None
        ),
        "EC-25 no prior window": sum(1 for r in rows if r["email_open_rate_prev_90d"] is None),
        "FR-10a stale rows": sum(1 for r in rows if r["data_as_of"] < ANCHOR.isoformat()),
    }
    assert all(n > 0 for n in counts.values()), counts


def test_a_small_account_exists_for_the_percentile_edge_case(rows):
    """EC-24 needs too few purchasers to rank, which a large account never gives."""
    purchasers = sum(1 for r in rows if r["account_id"] == "ACC_D" and r["total_orders"] > 0)
    assert 0 < purchasers < 20


def test_windowed_counts_never_exceed_the_lifetime_total(rows):
    for r in rows:
        assert r["orders_last_90d"] + r["orders_prev_90d"] <= r["total_orders"], r["customer_id"]


def test_purchase_fields_agree_about_whether_a_purchase_happened(rows):
    for r in rows:
        assert (r["last_purchase_at"] is not None) == (r["total_orders"] > 0), r["customer_id"]
