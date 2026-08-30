"""App-state persistence. The only module that writes campaign SQL.

Same discipline as `customer_repo`: complete static statements fixed at import,
every value bound as a parameter, and `account_id` required on every read so a
campaign belonging to someone else cannot be fetched even by id `[SEC-04]`,
`[AZ-05]`.

Sends and engagement events land with M8, where their callers appear. Writing
their API before that would mean guessing at signatures nothing calls yet.
"""

import json
import sqlite3
import uuid
from datetime import UTC, datetime

from texting_agent.schemas.campaign import CampaignState

_CAMPAIGN_COLUMNS = """
    campaign_id, account_id, state, goal, created_by, created_at, updated_at,
    content_hash, model_id, prompt_version, config_version,
    tokens_in, tokens_out, llm_cost_usd,
    excluded_stale_count, excluded_unknown_count, revised_from,
    failure_code, failure_detail
"""

_SQL: dict[str, str] = {
    "insert_campaign": (
        "INSERT INTO campaigns (campaign_id, account_id, state, goal, created_by, "
        "created_at, updated_at, prompt_version, config_version, "
        "excluded_stale_count, excluded_unknown_count, revised_from) "
        "VALUES (:campaign_id, :account_id, :state, :goal, :created_by, "
        ":created_at, :created_at, :prompt_version, :config_version, "
        ":excluded_stale_count, :excluded_unknown_count, :revised_from)"
    ),
    "get_campaign": (
        "SELECT" + _CAMPAIGN_COLUMNS
        + " FROM campaigns WHERE campaign_id = :campaign_id "
        "AND account_id = :account_id"
    ),
    "list_campaigns": (
        "SELECT" + _CAMPAIGN_COLUMNS
        + " FROM campaigns WHERE account_id = :account_id "
        "ORDER BY created_at DESC LIMIT :limit"
    ),
    "transition": (
        "UPDATE campaigns SET state = :to_state, updated_at = :now "
        "WHERE campaign_id = :campaign_id AND state = :from_state"
    ),
    "current_state": (
        "SELECT state FROM campaigns WHERE campaign_id = :campaign_id"
    ),
    "record_usage": (
        "UPDATE campaigns SET tokens_in = COALESCE(tokens_in, 0) + :tokens_in, "
        "tokens_out = COALESCE(tokens_out, 0) + :tokens_out, "
        "model_id = :model_id, updated_at = :now "
        "WHERE campaign_id = :campaign_id"
    ),
    "record_failure": (
        "UPDATE campaigns SET failure_code = :failure_code, "
        "failure_detail = :failure_detail, updated_at = :now "
        "WHERE campaign_id = :campaign_id"
    ),
    "insert_segment": (
        "INSERT INTO campaign_segments (segment_id, campaign_id, name, priority, "
        "predicate_json, playbook_id, offer_json, channels, customer_count, "
        "rationale) VALUES (:segment_id, :campaign_id, :name, :priority, "
        ":predicate_json, :playbook_id, :offer_json, :channels, :customer_count, "
        ":rationale)"
    ),
    "list_segments": (
        "SELECT segment_id, campaign_id, name, priority, predicate_json, "
        "playbook_id, offer_json, channels, customer_count, rationale "
        "FROM campaign_segments WHERE campaign_id = :campaign_id "
        "ORDER BY priority, name"
    ),
    "insert_target": (
        "INSERT INTO campaign_targets (campaign_id, segment_id, account_id, "
        "customer_id, was_lapsed) VALUES (:campaign_id, :segment_id, :account_id, "
        ":customer_id, :was_lapsed)"
    ),
    "list_targets": (
        "SELECT campaign_id, segment_id, account_id, customer_id, was_lapsed "
        "FROM campaign_targets WHERE campaign_id = :campaign_id "
        "ORDER BY customer_id"
    ),
    "count_targets": (
        "SELECT COUNT(*) AS n FROM campaign_targets WHERE campaign_id = :campaign_id"
    ),
    "insert_variant": (
        "INSERT INTO message_variants (variant_id, segment_id, channel, label, "
        "subject_template, body_template, cta_text, cta_url_key) "
        "VALUES (:variant_id, :segment_id, :channel, :label, :subject_template, "
        ":body_template, :cta_text, :cta_url_key)"
    ),
    "list_variants": (
        "SELECT v.variant_id, v.segment_id, v.channel, v.label, "
        "v.subject_template, v.body_template, v.cta_text, v.cta_url_key, "
        "s.name AS segment_name "
        "FROM message_variants v "
        "JOIN campaign_segments s ON s.segment_id = v.segment_id "
        "WHERE s.campaign_id = :campaign_id "
        "ORDER BY s.priority, v.channel, v.label"
    ),
    "update_segment_plan": (
        "UPDATE campaign_segments SET playbook_id = :playbook_id, "
        "offer_json = :offer_json, channels = :channels, rationale = :rationale "
        "WHERE segment_id = :segment_id"
    ),
    "set_hash": (
        "UPDATE campaigns SET content_hash = :content_hash, updated_at = :now "
        "WHERE campaign_id = :campaign_id"
    ),
    "insert_approval": (
        "INSERT INTO campaign_approvals (campaign_id, decision, approver_id, "
        "content_hash, reason, decided_at) VALUES (:campaign_id, :decision, "
        ":approver_id, :content_hash, :reason, :decided_at)"
    ),
    "list_approvals": (
        "SELECT campaign_id, decision, approver_id, content_hash, reason, "
        "decided_at FROM campaign_approvals WHERE campaign_id = :campaign_id "
        "ORDER BY decided_at"
    ),
    "insert_send": (
        "INSERT OR IGNORE INTO send_log (send_id, campaign_id, segment_id, "
        "variant_id, account_id, customer_id, channel, status, skip_reason, "
        "provider_message_id, error, attempted_at) "
        "VALUES (:send_id, :campaign_id, :segment_id, :variant_id, :account_id, "
        ":customer_id, :channel, :status, :skip_reason, :provider_message_id, "
        ":error, :attempted_at)"
    ),
    "list_sends": (
        "SELECT send_id, campaign_id, segment_id, variant_id, account_id, "
        "customer_id, channel, status, skip_reason, provider_message_id, error, "
        "attempted_at FROM send_log WHERE campaign_id = :campaign_id "
        "ORDER BY attempted_at, customer_id, channel"
    ),
    "count_recent_sends": (
        "SELECT COUNT(*) AS n FROM send_log WHERE account_id = :account_id "
        "AND customer_id = :customer_id AND status = 'SENT' "
        "AND attempted_at >= :since"
    ),
    "is_suppressed": (
        "SELECT 1 FROM suppressions WHERE account_id = :account_id "
        "AND customer_id = :customer_id AND channel = :channel"
    ),
    "insert_suppression": (
        "INSERT OR IGNORE INTO suppressions (account_id, customer_id, channel, "
        "reason, created_at) VALUES (:account_id, :customer_id, :channel, "
        ":reason, :created_at)"
    ),
    "insert_event": (
        "INSERT INTO engagement_events (event_id, send_id, event_type, revenue, "
        "occurred_at) VALUES (:event_id, :send_id, :event_type, :revenue, "
        ":occurred_at)"
    ),
    "list_events": (
        "SELECT e.event_id, e.send_id, e.event_type, e.revenue, e.occurred_at, "
        "s.campaign_id, s.segment_id, s.variant_id, s.customer_id, s.channel "
        "FROM engagement_events e JOIN send_log s ON s.send_id = e.send_id "
        "WHERE s.campaign_id = :campaign_id ORDER BY e.occurred_at"
    ),
    "get_send": (
        "SELECT send_id, campaign_id, account_id, customer_id, channel "
        "FROM send_log WHERE send_id = :send_id"
    ),
    "insert_run": (
        "INSERT INTO agent_runs (run_id, campaign_id, account_id, stage, model_id, "
        "tokens_in, tokens_out, latency_ms, status, error, trace_id, created_at) "
        "VALUES (:run_id, :campaign_id, :account_id, :stage, :model_id, "
        ":tokens_in, :tokens_out, :latency_ms, :status, :error, :trace_id, :created_at)"
    ),
    "list_runs": (
        "SELECT run_id, campaign_id, account_id, stage, model_id, tokens_in, "
        "tokens_out, latency_ms, status, error, trace_id, created_at "
        "FROM agent_runs WHERE campaign_id = :campaign_id ORDER BY created_at"
    ),
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


class CampaignRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # --- campaigns --------------------------------------------------------

    def create_campaign(self, account_id: str, goal: str, created_by: str,
                        prompt_version: str, config_version: str,
                        excluded_stale_count: int = 0,
                        excluded_unknown_count: int = 0,
                        revised_from: str | None = None) -> str:
        if not account_id:
            raise ValueError("account_id is required")
        campaign_id = str(uuid.uuid4())
        self._conn.execute(_SQL["insert_campaign"], {
            "campaign_id": campaign_id, "account_id": account_id,
            "state": CampaignState.RECEIVED.value, "goal": goal,
            "created_by": created_by, "created_at": _now(),
            "prompt_version": prompt_version, "config_version": config_version,
            "excluded_stale_count": excluded_stale_count,
            "excluded_unknown_count": excluded_unknown_count,
            "revised_from": revised_from,
        })
        self._conn.commit()
        return campaign_id

    def get(self, account_id: str, campaign_id: str) -> sqlite3.Row | None:
        """Scoped by account as well as id: a campaign belonging to another
        account is indistinguishable from one that does not exist `[AZ-05]`."""
        if not account_id:
            raise ValueError("account_id is required")
        return self._conn.execute(
            _SQL["get_campaign"],
            {"campaign_id": campaign_id, "account_id": account_id},
        ).fetchone()

    def list_for_account(self, account_id: str, limit: int = 50) -> list[sqlite3.Row]:
        if not account_id:
            raise ValueError("account_id is required")
        return self._conn.execute(
            _SQL["list_campaigns"], {"account_id": account_id, "limit": limit}
        ).fetchall()

    def try_transition(self, campaign_id: str, from_state: CampaignState,
                       to_state: CampaignState) -> bool:
        """Conditional UPDATE. Returns whether this caller won the race."""
        changed = self._conn.execute(_SQL["transition"], {
            "campaign_id": campaign_id, "from_state": from_state.value,
            "to_state": to_state.value, "now": _now(),
        }).rowcount
        self._conn.commit()
        return changed == 1

    def current_state(self, campaign_id: str) -> str | None:
        row = self._conn.execute(
            _SQL["current_state"], {"campaign_id": campaign_id}
        ).fetchone()
        return row["state"] if row else None

    def add_usage(self, campaign_id: str, tokens_in: int, tokens_out: int,
                  model_id: str) -> None:
        self._conn.execute(_SQL["record_usage"], {
            "campaign_id": campaign_id, "tokens_in": tokens_in,
            "tokens_out": tokens_out, "model_id": model_id, "now": _now(),
        })
        self._conn.commit()

    def record_failure(self, campaign_id: str, code: str, detail: str) -> None:
        self._conn.execute(_SQL["record_failure"], {
            "campaign_id": campaign_id, "failure_code": code,
            "failure_detail": detail, "now": _now(),
        })
        self._conn.commit()

    # --- segments and the frozen audience ---------------------------------

    def add_segment(self, campaign_id: str, name: str, priority: int,
                    predicate: dict, customer_count: int,
                    playbook_id: str = "", offer: dict | None = None,
                    channels: str = "", rationale: str | None = None) -> str:
        segment_id = str(uuid.uuid4())
        self._conn.execute(_SQL["insert_segment"], {
            "segment_id": segment_id, "campaign_id": campaign_id, "name": name,
            "priority": priority, "predicate_json": json.dumps(predicate),
            "playbook_id": playbook_id, "offer_json": json.dumps(offer or {}),
            "channels": channels, "customer_count": customer_count,
            "rationale": rationale,
        })
        self._conn.commit()
        return segment_id

    def list_segments(self, campaign_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            _SQL["list_segments"], {"campaign_id": campaign_id}
        ).fetchall()

    def freeze_targets(self, campaign_id: str, account_id: str, segment_id: str,
                       targets: list[tuple[str, bool]]) -> int:
        """Write the audience `[FR-42a]`.

        Rows here are what the approval hash covers. Send-time gates may mark a
        row skipped; nothing may ever add one, which is why this is called once,
        before the hash, and never again.
        """
        self._conn.executemany(_SQL["insert_target"], [
            {"campaign_id": campaign_id, "segment_id": segment_id,
             "account_id": account_id, "customer_id": customer_id,
             "was_lapsed": int(was_lapsed)}
            for customer_id, was_lapsed in targets
        ])
        self._conn.commit()
        return len(targets)

    def list_targets(self, campaign_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            _SQL["list_targets"], {"campaign_id": campaign_id}
        ).fetchall()

    def count_targets(self, campaign_id: str) -> int:
        return int(self._conn.execute(
            _SQL["count_targets"], {"campaign_id": campaign_id}
        ).fetchone()["n"])

    def set_plan(self, segment_id: str, playbook_id: str, offer: dict,
                 channels: list[str], rationale: str) -> None:
        self._conn.execute(_SQL["update_segment_plan"], {
            "segment_id": segment_id, "playbook_id": playbook_id,
            "offer_json": json.dumps(offer), "channels": ",".join(channels),
            "rationale": rationale,
        })
        self._conn.commit()

    # --- approval ---------------------------------------------------------

    def set_content_hash(self, campaign_id: str, content_hash: str) -> None:
        self._conn.execute(_SQL["set_hash"], {
            "campaign_id": campaign_id, "content_hash": content_hash, "now": _now(),
        })
        self._conn.commit()

    def record_decision(self, campaign_id: str, decision: str, approver_id: str,
                        content_hash: str, reason: str | None = None) -> None:
        """Who decided what, when, and over which hash `[FR-43]`.

        Append-only: a rejection followed by a later approval leaves both rows,
        because the history of a decision is part of the decision.
        """
        self._conn.execute(_SQL["insert_approval"], {
            "campaign_id": campaign_id, "decision": decision,
            "approver_id": approver_id, "content_hash": content_hash,
            "reason": reason, "decided_at": _now(),
        })
        self._conn.commit()

    def list_decisions(self, campaign_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            _SQL["list_approvals"], {"campaign_id": campaign_id}
        ).fetchall()

    # --- message variants -------------------------------------------------

    def add_variant(self, segment_id: str, channel: str, label: str,
                    body_template: str, subject_template: str | None = None,
                    cta_text: str | None = None,
                    cta_url_key: str | None = None) -> str:
        variant_id = str(uuid.uuid4())
        self._conn.execute(_SQL["insert_variant"], {
            "variant_id": variant_id, "segment_id": segment_id, "channel": channel,
            "label": label, "subject_template": subject_template,
            "body_template": body_template, "cta_text": cta_text,
            "cta_url_key": cta_url_key,
        })
        self._conn.commit()
        return variant_id

    def list_variants(self, campaign_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            _SQL["list_variants"], {"campaign_id": campaign_id}
        ).fetchall()

    # --- sending ----------------------------------------------------------

    def record_send(self, campaign_id: str, segment_id: str, account_id: str,
                    customer_id: str, channel: str, status: str,
                    variant_id: str | None = None, skip_reason: str | None = None,
                    provider_message_id: str | None = None,
                    error: str | None = None) -> str:
        """One row per attempt, whatever the outcome `[FR-51]`.

        INSERT OR IGNORE against UNIQUE(campaign, customer, channel), so a
        replayed send is a no-op rather than a second message.
        """
        send_id = str(uuid.uuid4())
        self._conn.execute(_SQL["insert_send"], {
            "send_id": send_id, "campaign_id": campaign_id,
            "segment_id": segment_id, "variant_id": variant_id,
            "account_id": account_id, "customer_id": customer_id,
            "channel": channel, "status": status, "skip_reason": skip_reason,
            "provider_message_id": provider_message_id, "error": error,
            "attempted_at": _now(),
        })
        self._conn.commit()
        return send_id

    def list_sends(self, campaign_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            _SQL["list_sends"], {"campaign_id": campaign_id}
        ).fetchall()

    def get_send(self, send_id: str) -> sqlite3.Row | None:
        return self._conn.execute(_SQL["get_send"], {"send_id": send_id}).fetchone()

    def recent_send_count(self, account_id: str, customer_id: str,
                          since: str) -> int:
        """Across ALL campaigns `[FR-54]`. A per-campaign cap would let ten
        campaigns each send politely and the customer receive ten messages."""
        return int(self._conn.execute(_SQL["count_recent_sends"], {
            "account_id": account_id, "customer_id": customer_id, "since": since,
        }).fetchone()["n"])

    # --- suppression ------------------------------------------------------

    def is_suppressed(self, account_id: str, customer_id: str,
                      channel: str) -> bool:
        return self._conn.execute(_SQL["is_suppressed"], {
            "account_id": account_id, "customer_id": customer_id,
            "channel": channel,
        }).fetchone() is not None

    def suppress(self, account_id: str, customer_id: str, channel: str,
                 reason: str) -> None:
        self._conn.execute(_SQL["insert_suppression"], {
            "account_id": account_id, "customer_id": customer_id,
            "channel": channel, "reason": reason, "created_at": _now(),
        })
        self._conn.commit()

    # --- engagement events ------------------------------------------------

    def record_event(self, send_id: str, event_type: str,
                     revenue: float | None = None,
                     occurred_at: str | None = None) -> str:
        """An unsubscribe or a bounce writes its suppression in the same
        transaction as the event `[FR-40a]`.

        Two statements would leave a window in which we know the customer
        unsubscribed and would still send to them.
        """
        event_id = str(uuid.uuid4())
        send = self.get_send(send_id)
        with self._conn:
            self._conn.execute(_SQL["insert_event"], {
                "event_id": event_id, "send_id": send_id,
                "event_type": event_type, "revenue": revenue,
                "occurred_at": occurred_at or _now(),
            })
            if send is not None and event_type in ("UNSUBSCRIBED", "BOUNCED"):
                self._conn.execute(_SQL["insert_suppression"], {
                    "account_id": send["account_id"],
                    "customer_id": send["customer_id"],
                    "channel": send["channel"], "reason": event_type,
                    "created_at": _now(),
                })
        return event_id

    def list_events(self, campaign_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            _SQL["list_events"], {"campaign_id": campaign_id}
        ).fetchall()

    # --- agent runs -------------------------------------------------------

    def record_run(self, campaign_id: str | None, account_id: str, stage: str,
                   model_id: str, tokens_in: int, tokens_out: int,
                   status: str, latency_ms: int | None = None,
                   error: str | None = None, trace_id: str | None = None) -> str:
        """Written by the orchestrator, never by the agent `[SEC-09]`."""
        run_id = str(uuid.uuid4())
        self._conn.execute(_SQL["insert_run"], {
            "run_id": run_id, "campaign_id": campaign_id, "account_id": account_id,
            "stage": stage, "model_id": model_id, "tokens_in": tokens_in,
            "tokens_out": tokens_out, "latency_ms": latency_ms, "status": status,
            "error": error, "trace_id": trace_id, "created_at": _now(),
        })
        self._conn.commit()
        return run_id

    def list_runs(self, campaign_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            _SQL["list_runs"], {"campaign_id": campaign_id}
        ).fetchall()
