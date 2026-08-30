"""Rendering `[FR-29]`-`[FR-32]`, `[SEC-11]`.

The rule under test everywhere here: a message with a visible placeholder must
never reach a customer. Failing closed is the only acceptable behaviour.
"""

from datetime import UTC, datetime

import pytest

from texting_agent.schemas.campaign import Channel
from texting_agent.schemas.churn import ValueTier
from texting_agent.schemas.customer import CustomerRecord
from texting_agent.services import rendering_service as rs
from texting_agent.services.rendering_service import (
    RenderContext,
    SkipCustomer,
    TemplateError,
    render,
    render_variant,
    validate_template,
)

NOW = datetime(2026, 6, 1, tzinfo=UTC)


class Variant:
    """The shape render_variant needs, without importing the agent schema."""

    def __init__(self, channel, body_template, subject_template=None,
                 cta_text=None, cta_url_key=None):
        self.channel = channel
        self.body_template = body_template
        self.subject_template = subject_template
        self.cta_text = cta_text
        self.cta_url_key = cta_url_key


@pytest.fixture(scope="module")
def config():
    return rs.load()


def customer(**overrides) -> CustomerRecord:
    base = {"account_id": "ACC_A", "customer_id": "C1",
            "customer_name": "Priya Sharma", "registration_date": NOW,
            "data_as_of": NOW, "total_orders": 4,
            "last_purchase_category": "outdoor"}
    return CustomerRecord.model_validate(base | overrides)


def context(**overrides) -> RenderContext:
    base = {"customer": customer(), "value_tier": ValueTier.VIP,
            "days_since_purchase": 63, "offer": {"value": 15, "code": "SAVE15"},
            "brand_name": "Northwind Retail",
            "unsubscribe_url": "https://example.test/u/abc"}
    return RenderContext(**(base | overrides))


# --- the allowlist ---------------------------------------------------------


def test_a_known_placeholder_resolves(config):
    assert render("Hi {{first_name}},", context(), Channel.EMAIL, config) == "Hi Priya,"


def test_an_unknown_placeholder_fails_the_template(config):
    """VR-08: the template is wrong for everybody, so this is not a per-customer
    skip."""
    with pytest.raises(TemplateError, match="account_balance"):
        render("Your balance is {{account_balance}}", context(), Channel.EMAIL, config)


def test_validation_catches_it_once_rather_than_per_customer(config):
    validate_template("Hi {{first_name}}, {{offer_value}}% off", config)
    with pytest.raises(TemplateError):
        validate_template("Hi {{ssn}}", config)


def test_whitespace_inside_the_braces_still_matches(config):
    assert render("Hi {{ first_name }}!", context(), Channel.EMAIL, config) == "Hi Priya!"


def test_a_placeholder_used_twice_resolves_both_times(config):
    rendered = render("{{first_name}}, really {{first_name}}", context(),
                      Channel.EMAIL, config)
    assert rendered == "Priya, really Priya"


# --- fallbacks and skipping ------------------------------------------------


def test_a_missing_value_uses_its_fallback(config):
    """FR-31: first_name falls back to a greeting that reads naturally."""
    rendered = render("Hi {{first_name}},", context(customer=customer(customer_name=None)),
                      Channel.EMAIL, config)
    assert rendered == "Hi there,"


def test_a_missing_value_with_no_fallback_skips_the_customer(config):
    """FR-31, VR-09: one recipient skipped with a reason, never a message with
    a hole in it."""
    with pytest.raises(SkipCustomer) as raised:
        render("It has been {{days_since_purchase}} days", context(days_since_purchase=None),
               Channel.EMAIL, config)
    assert raised.value.placeholder == "days_since_purchase"
    assert raised.value.reason == "UNRESOLVED_DAYS_SINCE_PURCHASE"


def test_a_missing_offer_code_skips_rather_than_inventing_one(config):
    with pytest.raises(SkipCustomer):
        render("Use code {{offer_code}}", context(offer={"value": 10}),
               Channel.EMAIL, config)


def test_no_rendered_output_ever_contains_a_raw_placeholder(config):
    """The property that matters, stated directly."""
    templates = [
        "Hi {{first_name}}, {{offer_value}}% off {{last_purchase_category}}",
        "{{brand_name}} misses you. {{unsubscribe_url}}",
        "You are a {{value_tier}} customer with {{total_orders}} orders",
    ]
    for template in templates:
        rendered = render(template, context(), Channel.EMAIL, config)
        assert "{{" not in rendered and "}}" not in rendered


# --- escaping --------------------------------------------------------------


def test_a_customer_value_cannot_inject_markup_into_an_email(config):
    """SEC-11: a name is untrusted input to the message body."""
    hostile = customer(customer_name="<script>alert(1)</script> Smith")
    rendered = render("Hi {{first_name}},", context(customer=hostile),
                      Channel.EMAIL, config)
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered


def test_control_characters_are_stripped_from_sms(config):
    hostile = customer(customer_name="Priya\x07\x00 Sharma")
    rendered = render("Hi {{first_name}},", context(customer=hostile),
                      Channel.SMS, config)
    assert "\x07" not in rendered and "\x00" not in rendered


def test_the_transform_takes_the_first_token_only(config):
    """A message addressed to "Priya Sharma," reads like a form letter."""
    assert render("{{first_name}}", context(), Channel.SMS, config) == "Priya"


# --- whole variants --------------------------------------------------------


def test_an_email_variant_renders_subject_body_and_footer(config):
    rendered = render_variant(
        Variant(Channel.EMAIL, subject_template="{{first_name}}, come back",
                body_template="We miss you. {{offer_value}}% off.",
                cta_text="Shop now", cta_url_key="shop_now"),
        context(), config)
    assert rendered.subject == "Priya, come back"
    assert "15% off" in rendered.body
    assert "https://example.test/u/abc" in rendered.body      # unsubscribe footer
    assert rendered.cta_url == "https://example.test/shop"


def test_an_sms_variant_gets_the_opt_out_appended(config):
    """FR-32: appended by code, so a model having an off day cannot omit it."""
    rendered = render_variant(
        Variant(Channel.SMS, body_template="We miss you, {{first_name}}."), context(),
        config)
    assert rendered.body.endswith("Reply STOP to opt out.")
    assert rendered.subject is None


def test_a_footer_the_model_already_wrote_is_not_duplicated(config):
    rendered = render_variant(
        Variant(Channel.SMS, body_template="Come back. Reply STOP to opt out."),
        context(), config)
    assert rendered.body.count("Reply STOP to opt out.") == 1


def test_an_unknown_cta_key_fails_rather_than_shipping_a_dead_link(config):
    with pytest.raises(TemplateError, match="cta_url_key"):
        render_variant(Variant(Channel.EMAIL, body_template="Hi",
                               cta_url_key="somewhere_else"), context(), config)


def test_a_template_carries_a_key_not_a_url(config):
    """VR-07: the model never writes a URL, so it cannot write a hostile one."""
    assert set(config.cta_urls) == {"shop_now", "view_offer", "account_home", "support"}
    for url in config.cta_urls.values():
        assert url.startswith("https://")


def test_the_shipped_config_loads_and_covers_the_fields_templates_need():
    config = rs.load()
    assert {"first_name", "offer_value", "unsubscribe_url"} <= set(config.placeholders)
    assert set(config.footers) == {Channel.EMAIL, Channel.SMS}
