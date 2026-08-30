"""Template rendering `[FR-29]`-`[FR-32]`, `[VR-08]`, `[VR-09]`, `[SEC-11]`.

Fails closed, always. A message that reaches a customer reading
"Hi {{first_name}}," is worse than a message that was never sent: the first
costs trust, the second costs one recipient. So an unknown placeholder fails the
campaign, and an unresolvable one skips that customer with a recorded reason.

This module is the other half of the no-fabrication design. The model writes the
template; every value substituted here comes from a `CustomerRecord` read from
the database. There is no path by which a customer fact in a sent message
originated in a model.
"""

import functools
import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from texting_agent.config import settings
from texting_agent.schemas.campaign import Channel
from texting_agent.schemas.churn import ValueTier
from texting_agent.schemas.customer import CustomerRecord

PLACEHOLDER = re.compile(r"\{\{\s*([a-z0-9_.]+)\s*\}\}", re.IGNORECASE)


class TemplateError(Exception):
    """An unknown placeholder. Fails the campaign, not one customer: the
    template is wrong for everybody `[VR-08]`."""


class SkipCustomer(Exception):
    """This customer has no value for a placeholder with no fallback. One
    recipient is skipped with a reason `[FR-31]`, `[VR-09]`."""

    def __init__(self, placeholder: str) -> None:
        super().__init__(f"unresolved placeholder: {placeholder}")
        self.placeholder = placeholder
        self.reason = f"UNRESOLVED_{placeholder.upper()}"


class Placeholder(BaseModel):
    source: str
    transform: str | None = None
    fallback: str | None = None


class RenderConfig(BaseModel):
    version: int
    placeholders: dict[str, Placeholder]
    footers: dict[Channel, str]
    cta_urls: dict[str, str] = Field(min_length=1)


def load(path: Path | str | None = None) -> RenderConfig:
    path = path or Path(settings.config_dir) / "placeholders.yaml"
    return RenderConfig.model_validate(
        yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    )


@functools.cache
def get() -> RenderConfig:
    return load()


@dataclass
class RenderContext:
    """Everything a template may draw on. Nothing else is reachable, so a
    placeholder cannot resolve to a field nobody meant to expose."""

    customer: CustomerRecord
    value_tier: ValueTier
    days_since_purchase: int | None = None
    offer: dict[str, Any] = field(default_factory=dict)
    brand_name: str = ""
    unsubscribe_url: str = ""

    def lookup(self, source: str) -> Any:
        if source.startswith("offer."):
            return self.offer.get(source.removeprefix("offer."))
        if source == "account.brand_name":
            return self.brand_name or None
        if source == "system.unsubscribe_url":
            return self.unsubscribe_url or None
        if source == "value_tier":
            return self.value_tier.value
        if source == "days_since_purchase":
            return self.days_since_purchase
        return getattr(self.customer, source, None)


def _transform(value: Any, transform: str | None) -> str:
    text = str(value)
    if transform == "first_token":
        return text.split()[0] if text.split() else text
    return text


def _escape(value: str, channel: Channel) -> str:
    """SEC-11. A customer value is untrusted input to the message body: a name
    of `<b>` must not become markup in an email."""
    if channel is Channel.EMAIL:
        return html.escape(value, quote=False)
    # SMS is plain text; strip the control characters that would break a body.
    return "".join(character for character in value if character.isprintable())


def placeholders_in(template: str) -> list[str]:
    return PLACEHOLDER.findall(template)


def validate_template(template: str, config: RenderConfig | None = None) -> None:
    """Campaign-level check, run once per template rather than per customer."""
    config = config or get()
    for key in placeholders_in(template):
        if key not in config.placeholders:
            raise TemplateError(f"unknown placeholder: {key}")


def render(template: str, context: RenderContext, channel: Channel,
           config: RenderConfig | None = None) -> str:
    config = config or get()
    result = template
    for key in dict.fromkeys(placeholders_in(template)):
        spec = config.placeholders.get(key)
        if spec is None:
            raise TemplateError(f"unknown placeholder: {key}")

        raw = context.lookup(spec.source)
        value = _transform(raw, spec.transform) if raw is not None else spec.fallback
        if value is None:
            # No value and no fallback. Skipping this customer is the only
            # honest option; sending the template as-is is not.
            raise SkipCustomer(key)
        result = PLACEHOLDER.sub(
            lambda match, replacement=_escape(str(value), channel):
                replacement if match.group(1) == key else match.group(0),
            result,
        )
    return result


@dataclass
class RenderedMessage:
    channel: Channel
    subject: str | None
    body: str
    cta_text: str | None = None
    cta_url: str | None = None


def render_variant(variant, context: RenderContext,
                   config: RenderConfig | None = None) -> RenderedMessage:
    """Render one variant for one customer, footer included `[FR-32]`.

    The footer is appended by code rather than asked for in the prompt, so an
    unsubscribe line cannot be forgotten by a model having an off day.
    """
    config = config or get()
    channel = variant.channel
    body = render(variant.body_template, context, channel, config)
    footer = render(config.footers[channel], context, channel, config)
    if footer not in body:
        body = f"{body}\n\n{footer}"

    subject = (render(variant.subject_template, context, channel, config)
               if variant.subject_template else None)
    cta_url = config.cta_urls.get(variant.cta_url_key) if variant.cta_url_key else None
    if variant.cta_url_key and cta_url is None:
        raise TemplateError(f"unknown cta_url_key: {variant.cta_url_key}")

    return RenderedMessage(channel=channel, subject=subject, body=body,
                           cta_text=variant.cta_text, cta_url=cta_url)
