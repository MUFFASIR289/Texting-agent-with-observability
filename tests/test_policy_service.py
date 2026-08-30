"""Policy `[FR-37]`, `[FR-38]`, `[VR-05]`-`[VR-07]`.

The rule these all serve: a violation is reported, never corrected. A campaign
quietly reduced to fit policy hides a broken prompt or a broken policy.
"""

import pytest
from pydantic import ValidationError

from texting_agent.schemas.agent_io import MessageVariant, Offer, RetentionPlan
from texting_agent.schemas.campaign import Channel, OfferType, PlaybookId
from texting_agent.schemas.churn import ValueTier
from texting_agent.services import playbook_service, policy_service, rendering_service
from texting_agent.services.policy_service import check_plan, check_variants


@pytest.fixture(scope="module")
def policy():
    return policy_service.load()


@pytest.fixture(scope="module")
def playbooks():
    return playbook_service.load()


@pytest.fixture(scope="module")
def render_config():
    return rendering_service.load()


def plan(offer_type=OfferType.PERCENTAGE_DISCOUNT, value=10,
         playbook=PlaybookId.DORMANT, channels=(Channel.EMAIL,)) -> RetentionPlan:
    return RetentionPlan(
        segment_name="S", playbook_id=playbook,
        offer=Offer(type=offer_type, value=value),
        channels=list(channels),
        channel_rationale="email open rate 0.31 against sms 0.04",
    )


def variant(channel=Channel.EMAIL, body="Come back soon.",
            subject="A subject", cta_key=None, cta_text=None) -> MessageVariant:
    return MessageVariant(channel=channel, body_template=body,
                          subject_template=subject if channel is Channel.EMAIL else None,
                          cta_url_key=cta_key, cta_text=cta_text)


def unvalidated_variant(body="Come back soon.",
                        cta_key="evil_site") -> MessageVariant:
    """A variant whose CTA key the schema would refuse.

    `cta_url_key` is a closed enum, so the model can no longer say this. Policy
    still checks it because a variant is also rebuilt from the database, where
    nothing re-runs schema validation.
    """
    return MessageVariant.model_construct(
        channel=Channel.EMAIL, body_template=body,
        subject_template="A subject", cta_url_key=cta_key, cta_text=None)


def rule_ids(violations) -> list[str]:
    return [v.rule_id for v in violations]


# --- offers ----------------------------------------------------------------


def test_an_offer_within_the_tier_cap_passes(policy, playbooks):
    assert check_plan(plan(value=10), ValueTier.STANDARD, playbooks, policy) == []


def test_a_fifty_percent_discount_is_rejected_by_rule_id(policy, playbooks):
    violations = check_plan(plan(value=50), ValueTier.STANDARD, playbooks, policy)
    assert rule_ids(violations) == ["OFFER_MAX_DISCOUNT"]
    assert violations[0].observed == 50
    assert violations[0].allowed == 15


def test_nothing_is_rewritten_to_fit(policy, playbooks):
    """FR-38: the plan comes back untouched; only a report is produced."""
    proposed = plan(value=50)
    check_plan(proposed, ValueTier.STANDARD, playbooks, policy)
    assert proposed.offer.value == 50


@pytest.mark.parametrize(
    ("tier", "cap"),
    [(ValueTier.VIP, 25), (ValueTier.HIGH_VALUE, 20),
     (ValueTier.STANDARD, 15), (ValueTier.LOW_VALUE, 10)],
)
def test_each_tier_has_its_own_cap(policy, playbooks, tier, cap):
    """A VIP is worth more, so more may be spent keeping them."""
    playbook = (PlaybookId.VIP_REACTIVATION
                if tier in (ValueTier.VIP, ValueTier.HIGH_VALUE)
                else PlaybookId.PRICE_SENSITIVE)
    assert check_plan(plan(value=cap, playbook=playbook), tier, playbooks, policy) == []
    over = check_plan(plan(value=cap + 1, playbook=playbook), tier, playbooks, policy)
    assert "OFFER_MAX_DISCOUNT" in rule_ids(over)


def test_an_offer_the_playbook_forbids_is_rejected(policy, playbooks):
    """The playbook bounds what may be offered; policy bounds how much."""
    violations = check_plan(plan(offer_type=OfferType.FIXED_DISCOUNT, value=5,
                                 playbook=PlaybookId.VIP_REACTIVATION),
                            ValueTier.VIP, playbooks, policy)
    assert rule_ids(violations) == ["OFFER_TYPE_NOT_IN_PLAYBOOK"]


def test_fixed_discounts_and_points_have_their_own_ceilings(policy, playbooks):
    fixed = check_plan(plan(offer_type=OfferType.FIXED_DISCOUNT, value=500,
                            playbook=PlaybookId.PRICE_SENSITIVE),
                       ValueTier.STANDARD, playbooks, policy)
    assert "OFFER_MAX_FIXED" in rule_ids(fixed)
    points = check_plan(plan(offer_type=OfferType.LOYALTY_POINTS, value=99_999,
                             playbook=PlaybookId.VIP_REACTIVATION),
                        ValueTier.VIP, playbooks, policy)
    assert "OFFER_MAX_POINTS" in rule_ids(points)


def test_an_offer_of_none_is_always_acceptable(policy, playbooks):
    assert check_plan(plan(offer_type=OfferType.NONE, value=0),
                      ValueTier.LOW_VALUE, playbooks, policy) == []


def test_every_violation_carries_what_was_seen_and_what_was_allowed(policy, playbooks):
    violation = check_plan(plan(value=99), ValueTier.LOW_VALUE, playbooks, policy)[0]
    assert violation.as_dict() == {
        "rule_id": "OFFER_MAX_DISCOUNT",
        "message": "99% exceeds the LOW_VALUE cap",
        "observed": 99, "allowed": 10,
    }


# --- variants --------------------------------------------------------------


def check(variants, policy, render_config, channels=(Channel.EMAIL,),
          per_channel=2, customer_ids=None):
    return check_variants(variants, list(channels), per_channel, policy,
                          render_config, customer_ids)


def test_two_distinct_variants_per_channel_pass(policy, render_config):
    assert check([variant(), variant(body="Different copy.")],
                 policy, render_config) == []


def test_one_variant_is_not_an_ab_test(policy, render_config):
    """FR-33."""
    violations = check([variant()], policy, render_config)
    assert rule_ids(violations) == ["VARIANT_COUNT"]
    assert violations[0].observed == 1


def test_an_email_without_a_subject_is_rejected(policy, render_config):
    bad = MessageVariant(channel=Channel.EMAIL, body_template="Body")
    violations = check([bad, variant()], policy, render_config)
    assert "EMAIL_NO_SUBJECT" in rule_ids(violations)


def test_an_over_long_sms_is_rejected(policy, render_config):
    long_body = "x" * (policy.messages.sms_max_chars + 1)
    violations = check([variant(channel=Channel.SMS, body=long_body),
                        variant(channel=Channel.SMS, body="short")],
                       policy, render_config, channels=(Channel.SMS,))
    assert "SMS_TOO_LONG" in rule_ids(violations)


@pytest.mark.parametrize("phrase", ["guaranteed", "risk free", "FINAL WARNING"])
def test_banned_phrases_are_caught_whatever_the_case(policy, render_config, phrase):
    violations = check([variant(body=f"This is {phrase}!"),
                        variant()], policy, render_config)
    assert "BANNED_PHRASE" in rule_ids(violations)


def test_an_unknown_cta_key_is_rejected(policy, render_config):
    violations = check([unvalidated_variant(), variant()],
                       policy, render_config)
    assert "CTA_KEY_NOT_ALLOWED" in rule_ids(violations)


def test_the_schema_refuses_an_invented_cta_key():
    """The stronger half of the same guarantee: policy rejects a bad key, and
    the schema stops the model producing one in the first place."""
    with pytest.raises(ValidationError):
        variant(cta_key="evil_site")


def test_an_unknown_placeholder_is_rejected(policy, render_config):
    violations = check([variant(body="Your {{account_balance}}"),
                        variant()], policy, render_config)
    assert "PLACEHOLDER_NOT_ALLOWED" in rule_ids(violations)


# --- forbidden literals `[VR-07]` ------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [("Reach us at priya@example.test", "LITERAL_EMAIL"),
     ("Call +1 555 123 4567 today", "LITERAL_PHONE"),
     ("Call (555) 123-4567 today", "LITERAL_PHONE"),
     ("Visit https://evil.test/steal now", "LITERAL_URL"),
     ("Regarding order #123456", "LITERAL_ORDER")],
)
def test_customer_particulars_in_content_are_rejected(policy, render_config,
                                                      body, expected):
    violations = check([variant(body=body), variant()],
                       policy, render_config)
    assert expected in rule_ids(violations)


def test_a_customer_id_in_content_is_rejected(policy, render_config):
    """The sharpest rule: customer_id is the only identifier the model actually
    receives, so it is the only one it could plausibly paste in."""
    violations = check([variant(body="Hi A01221, come back"),
                        variant()],
                       policy, render_config, customer_ids=["A01221", "A00002"])
    assert "LITERAL_CUSTOMER_ID" in rule_ids(violations)
    assert violations[0].observed == "A01221"


def test_placeholders_are_not_mistaken_for_literals(policy, render_config):
    """{{first_name}} is the correct way to address someone. If this rule fired
    on it, the only compliant template would be one that greets nobody."""
    good = [variant(body="Hi {{first_name}}, {{offer_value}}% off",
                    subject="{{first_name}}, come back"),
            variant(body="{{brand_name}} misses you")]
    assert check(good, policy, render_config, customer_ids=["A01221"]) == []


def test_a_url_inside_a_placeholder_is_fine(policy, render_config):
    """The unsubscribe URL arrives through a placeholder, so the literal-URL rule
    must not fire on the one link every message is required to carry."""
    assert check([variant(body="Bye. {{unsubscribe_url}}"),
                  variant(body="See you. {{unsubscribe_url}}")],
                 policy, render_config) == []


def test_several_violations_are_all_reported(policy, render_config):
    """An operator fixing one problem at a time is an operator running the
    campaign five times."""
    violations = check([unvalidated_variant(
        body="Guaranteed! Call 555-123-4567")], policy, render_config)
    assert set(rule_ids(violations)) >= {"VARIANT_COUNT", "BANNED_PHRASE",
                                         "LITERAL_PHONE", "CTA_KEY_NOT_ALLOWED"}


# --- configuration ---------------------------------------------------------


def test_the_shipped_policy_loads_and_covers_every_tier():
    config = policy_service.load()
    assert set(config.offers.max_discount_pct_by_tier) == set(ValueTier)


def test_a_tier_with_no_cap_is_refused(tmp_path):
    """VR-11: a customer in an uncapped tier could be offered anything."""
    import yaml

    from texting_agent.config import settings
    from pathlib import Path

    raw = yaml.safe_load((Path(settings.config_dir) / "policy.yaml").read_text())
    del raw["offers"]["max_discount_pct_by_tier"]["VIP"]
    broken = tmp_path / "policy.yaml"
    broken.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="VIP"):
        policy_service.load(broken)


def test_quiet_hours_are_deliberately_absent():
    """RV-A3: specified in two contradictory places, and with no scheduler there
    is nowhere to defer a send to. Its return is a post-MVP item."""
    from pathlib import Path

    from texting_agent.config import settings
    raw = (Path(settings.config_dir) / "policy.yaml").read_text()
    assert "quiet_hours" not in raw


def test_the_policy_is_validated_at_startup(monkeypatch, tmp_path):
    """VR-11: a bad policy is a failed boot, not a bad campaign hours later."""
    import yaml
    from pathlib import Path

    from fastapi.testclient import TestClient

    from texting_agent.config import settings
    from texting_agent.main import app

    raw = yaml.safe_load((Path(settings.config_dir) / "policy.yaml").read_text())
    raw["offers"]["max_fixed_discount"] = -1
    broken = tmp_path / "config"
    broken.mkdir()
    (broken / "policy.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    for name in ("scoring.yaml", "playbooks.yaml", "placeholders.yaml"):
        (broken / name).write_text(
            (Path(settings.config_dir) / name).read_text(encoding="utf-8"),
            encoding="utf-8")

    policy_service.get.cache_clear()
    monkeypatch.setattr(settings, "config_dir", str(broken))
    with pytest.raises(ValueError), TestClient(app):
        pass
    policy_service.get.cache_clear()
