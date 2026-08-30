"""The only path from a campaign to a customer `[FR-49]`-`[FR-54]`.

Two guarantees, in this order:

1. **Nothing is dispatched unless the approved hash still matches.** Recomputed
   over stored content, offers and `campaign_targets` before the first message.
   A mismatch aborts the entire send, not just the changed part - if the
   audience or the copy moved, the approval no longer describes what would go
   out `[FR-45]`, `[VR-10]`.
2. **Gates only remove recipients, never add.** The frozen list is the ceiling.
   That asymmetry is exactly what lets the hash cover the audience without
   breaking every time someone unsubscribes `[RV-C3]`, `[EC-27]`.

A customer who is skipped still gets a `send_log` row with the reason. A run
where 400 of 900 recipients silently vanished is indistinguishable from a broken
one.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import structlog

from texting_agent.database.repositories.campaign_repo import CampaignRepository
from texting_agent.database.repositories.customer_repo import CustomerRepository
from texting_agent.integrations.providers import (
    CircuitBreaker,
    EmailProvider,
    ProviderError,
    SMSProvider,
)
from texting_agent.orchestrator.approval import content_hash
from texting_agent.schemas.campaign import Channel
from texting_agent.schemas.churn import ValueTier
from texting_agent.services import policy_service, rendering_service
from texting_agent.services.rendering_service import RenderContext, SkipCustomer
from texting_agent.services.scoring_service import days_since

log = structlog.get_logger()


class HashMismatch(Exception):
    """The campaign changed after approval. Nothing is sent `[VR-10]`."""


class CircuitOpen(Exception):
    """The provider has failed repeatedly. Remaining recipients are skipped
    rather than thrown at a service that is plainly down `[EH-12]`."""


@dataclass
class SendReport:
    campaign_id: str
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)
    aborted: str | None = None

    @property
    def attempted(self) -> int:
        return self.sent + self.failed + self.skipped

    def skip(self, reason: str) -> None:
        self.skipped += 1
        self.skip_reasons[reason] = self.skip_reasons.get(reason, 0) + 1


def assign_variant(campaign_id: str, channel: str, customer_id: str,
                   variant_count: int) -> int:
    """Deterministic A/B assignment `[FR-53]`.

    The channel is inside the hash, so a customer receiving both email and SMS
    is not locked to label A in both. Without it the two experiments would be
    perfectly correlated and neither result would be clean `[RV-C9]`.
    """
    digest = hashlib.sha256(
        f"{campaign_id}|{channel}|{customer_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % variant_count


class CommunicationService:
    def __init__(self, campaigns: CampaignRepository,
                 customers: CustomerRepository,
                 email: EmailProvider, sms: SMSProvider,
                 max_attempts: int = 3, breaker_threshold: int = 5) -> None:
        self._campaigns = campaigns
        self._customers = customers
        self._email = email
        self._sms = sms
        self._max_attempts = max_attempts
        self._breaker = CircuitBreaker(breaker_threshold)

    def send(self, campaign_id: str, account_id: str,
             approved_hash: str) -> SendReport:
        report = SendReport(campaign_id=campaign_id)

        current = content_hash(self._campaigns, campaign_id)
        if current != approved_hash:
            # Abort everything. A partial send under a changed approval is worse
            # than no send: it is a send nobody authorised.
            raise HashMismatch(
                "the campaign changed after approval; nothing was sent")

        policy = policy_service.get()
        render_config = rendering_service.get()
        variants_by_segment = self._variants_by_segment(campaign_id)
        segments = {row["segment_id"]: row
                    for row in self._campaigns.list_segments(campaign_id)}
        window_start = (datetime.now(UTC)
                        - timedelta(days=policy.frequency.window_days)).isoformat()

        for target in self._campaigns.list_targets(campaign_id):
            customer_id = target["customer_id"]
            segment = segments.get(target["segment_id"])
            if segment is None:                       # pragma: no cover
                continue

            record = self._customers.get(account_id, customer_id)
            if record is None:
                # The customer left the source data between approval and send.
                self._record(report, campaign_id, target, account_id, "EMAIL",
                             "SKIPPED", skip_reason="CUSTOMER_NOT_FOUND")
                continue

            # Checked once per customer, not per channel: the cap counts
            # messages, and an EMAIL_SMS campaign spends two of them.
            already_sent = self._campaigns.recent_send_count(
                account_id, customer_id, window_start)

            for channel in _channels_of(segment):
                if already_sent >= policy.frequency.max_messages_per_customer:
                    self._record(report, campaign_id, target, account_id,
                                 channel.value, "SKIPPED",
                                 skip_reason="FREQUENCY_CAP")
                    continue

                blocked = self._gate(account_id, customer_id, channel, record)
                if blocked:
                    self._record(report, campaign_id, target, account_id,
                                 channel.value, "SKIPPED", skip_reason=blocked)
                    continue

                variants = variants_by_segment.get(
                    (target["segment_id"], channel.value), [])
                if not variants:
                    self._record(report, campaign_id, target, account_id,
                                 channel.value, "SKIPPED",
                                 skip_reason="NO_VARIANT_FOR_CHANNEL")
                    continue
                variant = variants[assign_variant(campaign_id, channel.value,
                                                  customer_id, len(variants))]

                try:
                    message = rendering_service.render_variant(
                        variant, self._context(record, segment, account_id),
                        render_config)
                except SkipCustomer as skip:
                    self._record(report, campaign_id, target, account_id,
                                 channel.value, "SKIPPED",
                                 skip_reason=skip.reason,
                                 variant_id=variant.variant_id)
                    continue

                if self._breaker.is_open:
                    self._record(report, campaign_id, target, account_id,
                                 channel.value, "SKIPPED",
                                 skip_reason="PROVIDER_UNAVAILABLE",
                                 variant_id=variant.variant_id)
                    continue

                self._dispatch(report, campaign_id, target, account_id, record,
                               channel, variant, message)
                already_sent += 1

        log.info("campaign.sent", campaign_id=campaign_id, sent=report.sent,
                 failed=report.failed, skipped=report.skipped)
        return report

    # --- internals --------------------------------------------------------

    def _gate(self, account_id: str, customer_id: str, channel: Channel,
              record) -> str | None:
        """Send-time state, which is why it is checked here and not at
        generation `[FR-40]`, `[EC-09]`, `[EC-10]`."""
        if self._campaigns.is_suppressed(account_id, customer_id, channel.value):
            return "SUPPRESSED"
        if channel is Channel.EMAIL:
            if not record.email_consent:
                return "NO_CONSENT"
            if not record.email:
                return "NO_CONTACT"
        else:
            if not record.sms_consent:
                return "NO_CONSENT"
            if not record.phone:
                return "NO_CONTACT"
        return None

    def _dispatch(self, report: SendReport, campaign_id: str, target,
                  account_id: str, record, channel: Channel, variant,
                  message) -> None:
        last_error = ""
        for _attempt in range(self._max_attempts):
            try:
                if channel is Channel.EMAIL:
                    result = self._email.send(record.email, message.subject or "",
                                              message.body)
                else:
                    result = self._sms.send(record.phone, message.body)
            except ProviderError as exc:
                last_error = str(exc)
                continue
            self._breaker.record(ok=True)
            self._record(report, campaign_id, target, account_id, channel.value,
                         "SENT", variant_id=variant.variant_id,
                         provider_message_id=result.message_id)
            return

        # Out of retries. Recorded as FAILED, never as SENT `[FR-52]`.
        self._breaker.record(ok=False)
        self._record(report, campaign_id, target, account_id, channel.value,
                     "FAILED", variant_id=variant.variant_id, error=last_error)

    def _record(self, report: SendReport, campaign_id: str, target,
                account_id: str, channel: str, status: str,
                skip_reason: str | None = None, variant_id: str | None = None,
                provider_message_id: str | None = None,
                error: str | None = None) -> None:
        self._campaigns.record_send(
            campaign_id=campaign_id, segment_id=target["segment_id"],
            account_id=account_id, customer_id=target["customer_id"],
            channel=channel, status=status, variant_id=variant_id,
            skip_reason=skip_reason, provider_message_id=provider_message_id,
            error=error,
        )
        if status == "SENT":
            report.sent += 1
        elif status == "FAILED":
            report.failed += 1
        else:
            report.skip(skip_reason or "SKIPPED")

    def _context(self, record, segment, account_id: str) -> RenderContext:
        import json

        offer = json.loads(segment["offer_json"] or "{}")
        return RenderContext(
            customer=record,
            value_tier=ValueTier.STANDARD,
            days_since_purchase=_whole_days(
                days_since(record.last_purchase_at, datetime.now(UTC))),
            offer={"value": offer.get("value"), "code": offer.get("code")},
            brand_name=account_id,
            unsubscribe_url=f"https://example.test/u/{record.customer_id}",
        )

    def _variants_by_segment(self, campaign_id: str) -> dict[tuple, list]:
        grouped: dict[tuple, list] = {}
        for row in self._campaigns.list_variants(campaign_id):
            grouped.setdefault((row["segment_id"], row["channel"]), []).append(
                _StoredVariant(row))
        # Sorted by label so the index a hash picks means the same thing on
        # every run `[FR-53]`.
        for variants in grouped.values():
            variants.sort(key=lambda v: v.label)
        return grouped


class _StoredVariant:
    """A stored row in the shape the renderer expects."""

    def __init__(self, row) -> None:
        self.variant_id = row["variant_id"]
        self.channel = Channel(row["channel"])
        self.label = row["label"]
        self.subject_template = row["subject_template"]
        self.body_template = row["body_template"]
        self.cta_text = row["cta_text"]
        self.cta_url_key = row["cta_url_key"]


def _channels_of(segment) -> list[Channel]:
    return [Channel(value) for value in (segment["channels"] or "").split(",")
            if value]


def _whole_days(value: float | None) -> int | None:
    return None if value is None else int(value)
