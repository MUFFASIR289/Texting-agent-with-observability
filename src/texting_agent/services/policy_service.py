"""Policy enforcement `[FR-37]`, `[FR-38]`, `[VR-06]`, `[VR-07]`.

Every violation is reported with its rule id, what was observed and what was
allowed. Nothing is ever corrected: a 50% discount silently reduced to 20% hides
either a broken prompt or a broken policy, and both need to be seen.

Enforcement is split by timing, and the split matters. Content properties are
fixed at generation and checked here. Suppression, consent and the frequency cap
are checked at **send**, because that state can change between approval and
dispatch `[FR-40]`, `[EC-10]`.

**Forbidden literals** are the sharpest rule `[VR-07]`. `customer_id` is the only
identifier the model actually receives, so it is the only one it could plausibly
paste into a template - which makes it the one worth checking hardest.
"""

import functools
import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from texting_agent.config import settings
from texting_agent.schemas.agent_io import MessageVariant, RetentionPlan
from texting_agent.schemas.campaign import Channel, OfferType
from texting_agent.schemas.churn import ValueTier
from texting_agent.services.playbook_service import PlaybookConfig
from texting_agent.services.rendering_service import RenderConfig, placeholders_in

# Deliberately broad. A false positive costs one regenerated campaign; a false
# negative sends a customer's phone number to a different customer.
EMAIL_LITERAL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")
PHONE_LITERAL = re.compile(r"(?<!\w)(?:\+\d[\d\s().-]{7,}|\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4})(?!\w)")
URL_LITERAL = re.compile(r"https?://\S+|www\.\S+")
ORDER_LITERAL = re.compile(r"\b(?:order|invoice|ref)\s*#?\s*\d{3,}\b", re.IGNORECASE)


@dataclass(frozen=True)
class Violation:
    rule_id: str
    message: str
    observed: object = None
    allowed: object = None

    def as_dict(self) -> dict:
        return {"rule_id": self.rule_id, "message": self.message,
                "observed": self.observed, "allowed": self.allowed}


class Offers(BaseModel):
    allowed_types: list[OfferType] = Field(min_length=1)
    max_discount_pct_by_tier: dict[ValueTier, float]
    max_fixed_discount: float = Field(gt=0)
    max_loyalty_points: float = Field(gt=0)


class Messages(BaseModel):
    sms_max_chars: int = Field(gt=0)
    email_footer_required: bool
    banned_phrases: list[str]
    allowed_cta_url_keys: list[str] = Field(min_length=1)


class Frequency(BaseModel):
    max_messages_per_customer: int = Field(gt=0)
    window_days: int = Field(gt=0)


class Analytics(BaseModel):
    attribution_window_days: int = Field(gt=0)


class Costs(BaseModel):
    email_cost: float = Field(ge=0)
    sms_cost: float = Field(ge=0)


class PolicyConfig(BaseModel):
    version: int
    offers: Offers
    messages: Messages
    frequency: Frequency
    analytics: Analytics
    costs: Costs


def load(path: Path | str | None = None) -> PolicyConfig:
    path = path or Path(settings.config_dir) / "policy.yaml"
    config = PolicyConfig.model_validate(
        yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    )
    # Every tier needs a cap, or a customer in an uncapped tier could be offered
    # anything at all.
    missing = [t for t in ValueTier if t not in config.offers.max_discount_pct_by_tier]
    if missing:
        raise ValueError(f"no discount cap for tiers: {[t.value for t in missing]}")
    return config


@functools.cache
def get() -> PolicyConfig:
    return load()


# --- business rules `[FR-36]`, `[VR-05]` -----------------------------------


def check_plan(plan: RetentionPlan, dominant_tier: ValueTier,
               playbooks: PlaybookConfig, policy: PolicyConfig) -> list[Violation]:
    violations: list[Violation] = []
    offer = plan.offer

    if not playbooks.allows(plan.playbook_id, offer.type):
        violations.append(Violation(
            "OFFER_TYPE_NOT_IN_PLAYBOOK",
            f"{plan.playbook_id.value} does not permit {offer.type.value}",
            observed=offer.type.value,
            allowed=[o.value for o in
                     playbooks.playbooks[plan.playbook_id].allowed_offer_types],
        ))

    if offer.type not in policy.offers.allowed_types:
        violations.append(Violation(
            "OFFER_TYPE_NOT_ALLOWED",
            f"{offer.type.value} is not an allowed offer type",
            observed=offer.type.value,
            allowed=[o.value for o in policy.offers.allowed_types],
        ))

    if offer.type is OfferType.PERCENTAGE_DISCOUNT:
        cap = policy.offers.max_discount_pct_by_tier[dominant_tier]
        if offer.value > cap:
            violations.append(Violation(
                "OFFER_MAX_DISCOUNT",
                f"{offer.value:g}% exceeds the {dominant_tier.value} cap",
                observed=offer.value, allowed=cap,
            ))
    elif offer.type is OfferType.FIXED_DISCOUNT:
        if offer.value > policy.offers.max_fixed_discount:
            violations.append(Violation(
                "OFFER_MAX_FIXED", f"a fixed discount of {offer.value:g} is too large",
                observed=offer.value, allowed=policy.offers.max_fixed_discount,
            ))
    elif offer.type is OfferType.LOYALTY_POINTS:
        if offer.value > policy.offers.max_loyalty_points:
            violations.append(Violation(
                "OFFER_MAX_POINTS", f"{offer.value:g} points is too many",
                observed=offer.value, allowed=policy.offers.max_loyalty_points,
            ))

    if not plan.channels:
        violations.append(Violation("PLAN_NO_CHANNEL",
                                    "a plan must select at least one channel"))
    return violations


def check_variants(variants: list[MessageVariant], channels: list[Channel],
                   variants_per_channel: int, policy: PolicyConfig,
                   render_config: RenderConfig,
                   customer_ids: list[str] | None = None) -> list[Violation]:
    violations: list[Violation] = []

    for channel in channels:
        for_channel = [v for v in variants if v.channel is channel]
        if len(for_channel) < variants_per_channel:
            # FR-33: fewer than two variants is not an A/B test, it is a send.
            violations.append(Violation(
                "VARIANT_COUNT",
                f"{channel.value} needs {variants_per_channel} variants to compare",
                observed=len(for_channel), allowed=variants_per_channel,
            ))
        labels = [v.label for v in for_channel]
        if len(set(labels)) != len(labels):
            violations.append(Violation(
                "VARIANT_LABELS_NOT_DISTINCT",
                f"{channel.value} variants must have distinct labels",
                observed=labels,
            ))

    for variant in variants:
        violations += _check_variant(variant, policy, render_config, customer_ids or [])
    return violations


def _check_variant(variant: MessageVariant, policy: PolicyConfig,
                   render_config: RenderConfig,
                   customer_ids: list[str]) -> list[Violation]:
    violations: list[Violation] = []
    where = f"{variant.channel.value} variant {variant.label}"
    text = " ".join(filter(None, [variant.subject_template, variant.body_template,
                                  variant.cta_text]))

    if variant.channel is Channel.SMS:
        # The rendered body is longer than the template once values land, but the
        # template length is what we can check before we know the customer.
        if len(variant.body_template) > policy.messages.sms_max_chars:
            violations.append(Violation(
                "SMS_TOO_LONG", f"{where} exceeds the SMS length limit",
                observed=len(variant.body_template),
                allowed=policy.messages.sms_max_chars,
            ))
    elif variant.subject_template is None:
        violations.append(Violation("EMAIL_NO_SUBJECT",
                                    f"{where} has no subject line"))

    lowered = text.lower()
    for phrase in policy.messages.banned_phrases:
        if phrase.lower() in lowered:
            violations.append(Violation(
                "BANNED_PHRASE", f"{where} contains a banned phrase",
                observed=phrase,
            ))

    if variant.cta_url_key and variant.cta_url_key not in policy.messages.allowed_cta_url_keys:
        violations.append(Violation(
            "CTA_KEY_NOT_ALLOWED", f"{where} uses an unknown CTA key",
            observed=variant.cta_url_key,
            allowed=policy.messages.allowed_cta_url_keys,
        ))

    for placeholder in placeholders_in(text):
        if placeholder not in render_config.placeholders:
            violations.append(Violation(
                "PLACEHOLDER_NOT_ALLOWED", f"{where} uses an unknown placeholder",
                observed=placeholder,
                allowed=sorted(render_config.placeholders),
            ))

    violations += _forbidden_literals(where, text, customer_ids)
    return violations


def _forbidden_literals(where: str, text: str,
                        customer_ids: list[str]) -> list[Violation]:
    """VR-07. Content the model wrote must contain no customer particulars.

    Placeholders are stripped first: `{{first_name}}` is the correct way to
    address someone, and it must not be mistaken for a literal.
    """
    stripped = re.sub(r"\{\{[^}]*\}\}", " ", text)
    found: list[Violation] = []

    for pattern, rule_id, description in (
        (EMAIL_LITERAL, "LITERAL_EMAIL", "an email address"),
        (PHONE_LITERAL, "LITERAL_PHONE", "a phone number"),
        (URL_LITERAL, "LITERAL_URL", "a URL"),
        (ORDER_LITERAL, "LITERAL_ORDER", "an order number"),
    ):
        match = pattern.search(stripped)
        if match:
            found.append(Violation(
                rule_id, f"{where} contains {description}",
                observed=match.group(0)[:60],
            ))

    # The one identifier the model is actually given, so the one it could paste.
    for customer_id in customer_ids:
        if customer_id and customer_id in stripped:
            found.append(Violation(
                "LITERAL_CUSTOMER_ID", f"{where} contains a customer id",
                observed=customer_id,
            ))
            break
    return found
