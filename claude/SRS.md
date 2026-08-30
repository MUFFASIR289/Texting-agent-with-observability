# Software Requirements Specification
## Texting Agent

| Field | Value |
|---|---|
| Document | SRS |
| Version | 1.0 |
| Date | 2026-08-29 |
| Related | [PRD.md](PRD.md) · [TRD.md](TRD.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) · [UIUX.md](UIUX.md) |

**Scope of this document.** *What* the system must do, with each requirement
verifiable. *How* it is built is in the TRD. Every requirement below carries an ID,
a priority (`MUST` = MVP-blocking, `SHOULD` = MVP-desirable, `MAY` = post-MVP), and a
verification method (`T` = automated test, `I` = inspection/manual demo,
`A` = analysis of traces or logs).

---

## 1. Definitions

| Term | Meaning |
|---|---|
| **Account** | A tenant business. Every customer record belongs to exactly one account. |
| **Scope** | The set of `account_id` values a caller is authorised for. Derived from the API key, never from request body or model output. |
| **Agent** | The single `TextingAgent`. All LLM reasoning happens here. |
| **Stage** | One structured LLM call with a fixed input contract and a Pydantic output schema. |
| **Tool** | A read-only, parameterised function the model may call. |
| **Playbook** | A pre-approved retention strategy definition loaded from configuration. |
| **Variant** | One email or SMS **template** with allow-listed placeholders. |
| **Rendering** | Deterministic substitution of customer values into a variant at send time. |
| **Risk score** | A 0–1 heuristic **ranking**, not a calibrated probability. |
| **Agent DB** | `data/customer_agent.db` — one table, opened read-only. |
| **App DB** | `data/app.db` — campaign and operational state, read-write, unreachable from any agent tool. |

---

## 2. System Context

```
 API client (operator / approver / analyst)
        |  X-API-Key
        v
 FastAPI  --->  Auth & Scope Resolver  --->  Orchestrator (state machine)
                                                   |
                    +------------------------------+-------------------------+
                    |                    |                    |              |
              Deterministic          Agent (LLM)          Policy &        Communication
              services:              5 stages +           validation      service ->
              scoring,               tool loop            engine          mock adapters
              segmentation,               |
              analytics                   | read-only tools
                                          v
                              Agent DB: customer_agent_records
                              (single table, PRAGMA query_only)

 App DB (campaigns, variants, approvals, sends, events)  <-- services only, never the agent
 Grafana Cloud  <-- OTLP over HTTPS (traces, metrics, logs)
```

---

## 3. Functional Requirements

### 3.1 Data & Churn Detection

| ID | Priority | Requirement | Verify |
|---|---|---|---|
| FR-01 | MUST | The system stores customer behavioural records in exactly one table, `customer_agent_records`, in a dedicated database file that contains no other table. | T |
| FR-02 | MUST | Every record carries a non-null `account_id` and a `customer_id` unique within its account. | T |
| FR-03 | MUST | A seed script generates reproducible synthetic data: 3 accounts × 5,000 customers plus one 12-customer account = 15,012 records, fixed random seed, spanning all risk levels and value tiers, and including the EC-03, EC-04, EC-05, EC-23, EC-24 and EC-25 edge cases, plus customers sparse enough that scoring must return `UNKNOWN` `[FR-04c]`. EC-24 (too few purchasers for percentile tiering) requires the small account: a low *share* of purchasers in a large account still yields hundreds. | T |
| FR-04 | MUST | A churn scoring service computes, per customer, a 0–1 `churn_score`, a `churn_risk_level` in {LOW, MEDIUM, HIGH, CRITICAL, UNKNOWN}, and an ordered list of reason codes with evidence. | T |
| FR-04a | MUST | Scoring includes **trend** signals computed from prior-period columns: engagement change and 90-day order change. Trend signals carry at least 30% of total weight. | T |
| FR-04b | MUST | `cart_abandonment_count_90d` and `support_issue_count_90d` are trailing-90-day counters, not lifetime totals. | T |
| FR-04c | MUST | A customer with fewer than `min_signals_required` usable signals is `UNKNOWN` with a null score, is counted and reported, and is excluded from campaign targeting. | T |
| FR-04d | MUST | The engagement signal is channel-aware: it takes the better of email and SMS engagement, each normalised against its own baseline. Value does not contribute to the churn score at all. | T |
| FR-05 | MUST | Signal weights, normalisation horizons and risk-level thresholds are read from configuration, not hard-coded. Changing configuration changes output with no code change. | T |
| FR-06 | MUST | Reason codes are drawn from a closed enum, one per scoring signal: `DORMANCY`, `PURCHASE_GAP`, `PURCHASE_DECLINE`, `ENGAGEMENT_DECLINE`, `LOW_ENGAGEMENT`, `LOGIN_LAPSE`, `CART_ABANDONMENT`, `SUPPORT_FRICTION`. `ENGAGEMENT_DECLINE` is emitted only when prior-period data exists; otherwise `LOW_ENGAGEMENT`. | T |
| FR-07 | MUST | Each reason code returned is accompanied by the field values that produced it (e.g. `days_since_purchase=63`, `expected_interval_days=21`). | T |
| FR-08 | MUST | Time-derived values (`days_since_activity`, `days_since_purchase`, `days_since_login`) are computed at read time from stored timestamps, never read from a stored stale column. | T |
| FR-09 | MUST | A value tiering service assigns VIP / HIGH_VALUE / STANDARD / LOW_VALUE using percentile ranks over **purchasers only**, computed within the account, so no currency threshold is hard-coded. | T |
| FR-09a | MUST | Customers with `total_orders = 0` or `total_spend = 0` are assigned `LOW_VALUE` directly, without entering the percentile ranking. | T |
| FR-09b | MUST | If an account has fewer than `min_purchasers_for_tiering` purchasers, all purchasers are `STANDARD` and the campaign response states why. | T |
| FR-10 | SHOULD | Records expose `data_as_of`; a record older than a configured freshness window is flagged `stale=true` in tool output. | T |
| FR-10a | MUST | Stale records are scored and reported but excluded from campaign targeting; the excluded count is surfaced on the campaign. | T |
| FR-11 | MUST | The LLM never computes a churn score, a risk level, a value tier or any percentage. | T, I |

### 3.2 Agent Tools

| ID | Priority | Requirement | Verify |
|---|---|---|---|
| FR-12 | MUST | The model is exposed exactly these tools: `get_churn_summary`, `get_churn_candidates`, `get_customer_behavior`, `get_segment_statistics`, and optionally `search_web`. No others. | T |
| FR-13 | MUST | No tool accepts `account_id`, a table name, a column list, a SQL fragment, or any free-form query string. Scope is bound by the orchestrator at toolset construction. | T |
| FR-14 | MUST | Tool outputs are typed Pydantic models and exclude `customer_name`, `email` and `phone` under all conditions. | T |
| FR-15 | MUST | `get_churn_candidates` caps results at a configurable maximum (default 20, hard ceiling 50) and returns aggregates alongside the sample. | T |
| FR-16 | MUST | All tool SQL is parameterised. String interpolation of user or model input into SQL is absent from the codebase. | T, I |
| FR-17 | MUST | A tool error returns a structured error object to the model; it never returns a stack trace, a SQL string, or a file path. | T |
| FR-18 | SHOULD | `search_web` is disabled by default, is timeout-bounded, caches by query, and its failure is logged and skipped without failing the caller. | T |

### 3.3 Agent Reasoning Stages

| ID | Priority | Requirement | Verify |
|---|---|---|---|
| FR-19 | MUST | Exactly one agent class exists. No second agent, sub-agent, or agent-to-agent delegation exists in the codebase. | I |
| FR-20 | MUST | The agent runs five stages, each a structured-output call with its own Pydantic schema: `ANALYZE`, `SEGMENT`, `PLAN`, `GENERATE`, `OPTIMIZE`. | T |
| FR-21 | MUST | `SEGMENT` returns segment **definitions** as structured predicates over risk level, value tier and reason codes. The model never receives or emits a list of individual customer assignments. | T |
| FR-22 | MUST | Deterministic code evaluates each predicate against scored records to assign customers to segments. | T |
| FR-23 | MUST | `PLAN` selects a `playbook_id` from the configured playbook enum. A playbook not present in configuration is a validation failure. | T |
| FR-23a | MUST | `RetentionPlan` carries no message-count or follow-up fields. v1 sends exactly one message per selected channel. | T |
| FR-24 | MUST | `PLAN` selects channels from {EMAIL, SMS, EMAIL_SMS}. The choice must be justified by the segment's aggregate `email_open_rate` / `sms_response_rate`, and validation rejects a channel whose engagement is below the configured floor when a better channel exists. | T |
| FR-25 | MUST | `GENERATE` returns templates only. Any literal resembling a customer name, email address, phone number, order number **or `customer_id`** in generated content is a validation failure. | T |
| FR-26 | MUST | `OPTIMIZE` receives only pre-computed metrics and a statistical verdict; it must not be asked to compute rates or significance. | T, I |
| FR-27 | MUST | Every stage enforces a per-run token budget; exceeding it fails the run with a clear error rather than continuing. | T |
| FR-28 | SHOULD | Stage prompts and instructions live in dedicated modules, are versioned, and the version is recorded on the campaign for reproducibility. | I |

### 3.4 Message Generation & Rendering

| ID | Priority | Requirement | Verify |
|---|---|---|---|
| FR-29 | MUST | Placeholders are restricted to a configured allowlist. Any other placeholder fails validation. | T |
| FR-30 | MUST | Rendering fails closed: an unresolved placeholder aborts that customer's message and records a skip reason; it never sends a message containing a raw placeholder. | T |
| FR-31 | MUST | A null customer value uses the placeholder's configured fallback; if the placeholder has no fallback, that customer is skipped with a recorded reason. | T |
| FR-32 | MUST | Email variants carry a subject, a body, a CTA and an unsubscribe footer. SMS variants carry a body within the configured character limit and an opt-out token. | T |
| FR-33 | MUST | Each segment produces at least 2 variants per selected channel to support A/B testing. | T |
| FR-34 | SHOULD | `GET /campaigns/{id}/messages` returns each variant together with a preview rendered against a real in-scope customer. | T |

### 3.5 Validation & Policy

| ID | Priority | Requirement | Verify |
|---|---|---|---|
| FR-35 | MUST | Every LLM output passes Pydantic validation before any further use. A parse failure retries once, then fails the stage. | T |
| FR-36 | MUST | Business-rule validation runs after schema validation: segment sizes non-zero, playbook exists, channels non-empty, variant counts satisfied, offer type valid for the playbook. | T |
| FR-37 | MUST | Policy validation enforces, from configuration: maximum discount value per value tier, allowed offer types, SMS length limit, mandatory unsubscribe footer, banned phrase list, forbidden literals, and the CTA URL-key allowlist. Quiet hours are **not** part of v1 — there is no scheduler to defer to. | T |
| FR-38 | MUST | A policy violation moves the campaign to `FAILED` with a machine-readable list of violated rule IDs. The system never silently rewrites the offer to fit policy. | T |
| FR-39 | MUST | Validation results are persisted with the campaign and returned by the campaign detail endpoint. | T |
| FR-40 | MUST | The suppression list (unsubscribed, bounced, opted-out, or missing consent for the channel) is checked at **send** time, not at generation time. | T |
| FR-40a | MUST | An `UNSUBSCRIBED` or `BOUNCED` engagement event writes a `suppressions` row for that customer and channel in the same transaction that records the event. | T |

### 3.6 Human Approval

| ID | Priority | Requirement | Verify |
|---|---|---|---|
| FR-41 | MUST | No message is dispatched by any code path without a recorded approval for that campaign. | T |
| FR-42 | MUST | On reaching `VALIDATED`, the orchestrator computes a SHA-256 hash over the canonical JSON of all variants, the offer **and the frozen recipient list**, stores it, and transitions to `AWAITING_APPROVAL`. | T |
| FR-42a | MUST | The resolved audience is frozen into `campaign_targets` before hashing. Send-time gates may skip rows; no code path may add one. | T |
| FR-43 | MUST | `POST /campaigns/{id}/approve` records approver identity, timestamp and the content-and-audience hash then in effect. | T |
| FR-44 | MUST | Approval is idempotent and state-guarded: approving a campaign not in `AWAITING_APPROVAL` returns 409 with no side effect. | T |
| FR-45 | MUST | At send time the system recomputes the hash over stored content, offer and `campaign_targets`, and aborts the entire send if it differs from the approved hash. | T |
| FR-46 | MUST | Only a caller whose key carries the `approver` role may approve or reject; an `operator` receives 403. | T |
| FR-47 | SHOULD | `POST /campaigns/{id}/reject` accepts a reason, stores it, and moves the campaign to `REJECTED` (terminal). | T |
| FR-48 | SHOULD | `POST /campaigns/{id}/cancel` moves any non-terminal campaign to `CANCELLED`. | T |
| FR-48a | SHOULD | `POST /campaigns/{id}/revise` on a `REJECTED` or `FAILED` campaign creates a new campaign in `RECEIVED` with `revised_from` set, passing the rejection reason to the agent as operator feedback. The original stays terminal. | T |

### 3.7 Communication

| ID | Priority | Requirement | Verify |
|---|---|---|---|
| FR-49 | MUST | The agent has no direct access to any provider. Sending is reachable only through the communication service. | I, T |
| FR-50 | MUST | `EmailProvider` and `SMSProvider` are abstract interfaces; v1 ships mock implementations selected by configuration. | I |
| FR-51 | MUST | Every send attempt writes a row recording customer, channel, variant, provider message id, status and timestamp — including failures and skips with their reason. | T |
| FR-52 | MUST | A provider failure is recorded as failed and retried per the configured retry policy. A failed send is never recorded as sent. | T |
| FR-53 | MUST | Customers are assigned to A/B variants by a deterministic hash over campaign id, **channel** and customer id, so assignment is reproducible and independent between channels. | T |
| FR-54 | MUST | Frequency cap: a customer who has received at least N messages in the last M days (configured) is skipped with reason `FREQUENCY_CAP`. | T |
| FR-55 | SHOULD | An event simulator generates delivered / opened / clicked / converted / unsubscribed / bounced events with configurable per-segment rates and a fixed seed, so analytics is exercised end to end. | T |

### 3.8 Analytics & Optimization

| ID | Priority | Requirement | Verify |
|---|---|---|---|
| FR-56 | MUST | The analytics service computes in Python: sent, delivered, opened, clicked, converted, unsubscribed, bounced, revenue; and the rates delivery, open, click-through, conversion, unsubscribe; plus gross attributed ROI and reactivation rate. | T |
| FR-56a | MUST | A conversion counts toward revenue only if it occurred within `attribution_window_days` of that customer's send. | T |
| FR-56b | MUST | `campaign_cost` includes per-message provider cost, discount liability on conversions, and LLM spend for the campaign. | T |
| FR-56c | MUST | The ROI metric is named `gross_attributed_roi` and every response carries a basis note stating it is not incremental because there is no holdout group. | T |
| FR-56d | MUST | `reactivation_rate` is computed only over targets flagged `was_lapsed` at freeze time, so it is not arithmetically identical to `conversion_rate`. | T |
| FR-57 | MUST | Rates use an explicit denominator documented per metric, and a zero denominator yields `null`, never a division error or a zero that reads as a real value. | T |
| FR-58 | MUST | Metrics are available per campaign, per segment and per variant. | T |
| FR-59 | MUST | A/B comparison uses a two-proportion z-test and a configurable minimum-sample gate. Below the gate the verdict is `INSUFFICIENT_DATA`. | T |
| FR-60 | MUST | When the verdict is `INSUFFICIENT_DATA`, the optimization output must not name a winning variant. | T |
| FR-61 | MUST | The optimization recommendation references the metric values it is based on and proposes a concrete next experiment. | T |
| FR-62 | SHOULD | Campaign cost is computed from configured per-message costs plus discount liability on converted customers, so ROI is reproducible. | T |

### 3.9 API

| ID | Priority | Requirement | Verify |
|---|---|---|---|
| FR-63 | MUST | `POST /campaigns` creates a campaign, runs the pipeline, and returns the campaign with its terminal-for-now state. | T |
| FR-63a | MUST | `POST /campaigns/{id}/send` requires state `APPROVED`, re-verifies the content-and-audience hash, and dispatches through the communication service. Any other state returns 409. | T |
| FR-63b | MUST | `POST /campaigns` and `POST /agent/query` require `account_id` in the body: 400 if omitted, 403 if outside the caller's scope. Exactly one account is in scope for any agent run. | T |
| FR-63c | SHOULD | `POST /campaigns/{id}/simulate-events` generates engagement events for the closed-loop demo and returns 404 unless `ENV=dev`. | T |
| FR-64 | MUST | `GET /campaigns/{id}` and the sub-resources `/customers`, `/segments`, `/strategy`, `/messages`, `/metrics`, `/optimization` are available and scope-checked. | T |
| FR-65 | MUST | `POST /agent/query` accepts a natural-language query and returns a grounded answer plus the tool calls made. | T |
| FR-66 | MUST | `account_id` supplied in a request body is ignored unless it is within the caller's scope; if outside scope the request is rejected 403. | T |
| FR-67 | MUST | `GET /health` reports service, agent-DB read-only status, app-DB writability and configuration validity, without leaking secrets. | T |
| FR-68 | MUST | Every response for a resource outside the caller's scope is 404 or 403 — never a partially filtered result. | T |
| FR-69 | SHOULD | All endpoints are documented via OpenAPI with example payloads. | I |

---

## 4. Permissions Model

### 4.1 Principals

| Principal | Authenticates via | Scope source |
|---|---|---|
| API client | `X-API-Key` header | Static key-to-`{account_ids, role}` mapping loaded from configuration/environment. A key may hold several accounts; an agent run is bound to exactly one, named in the request body |
| Agent | Not a principal. It has no identity and no credentials. | Inherits the caller's scope, injected by the orchestrator |

### 4.2 Roles

| Role | Read campaigns & intelligence | Create campaign | Approve / reject | Cancel |
|---|---|---|---|---|
| `operator` | Yes | Yes | **No** | Yes |
| `approver` | Yes | Yes | Yes | Yes |

### 4.3 Database permissions

| Component | Agent DB | App DB |
|---|---|---|
| Agent tools | `SELECT` only, on `customer_agent_records` only, via a read-only connection | **No connection exists** |
| Scoring / segmentation services | `SELECT` | — |
| Campaign / analytics services | — | Read-write |
| Seed script | Read-write (separate process, separate connection, not importable by the agent) | Read-write |

**Rules that hold in all cases:**
- The agent's connection is opened with `mode=ro` and `PRAGMA query_only=ON`.
- The agent's database file physically contains one table, so a hypothetical
  escape from the tool layer still reaches nothing else.
- No code path grants the model an `account_id` parameter, a raw query, or a
  write of any kind.

---

## 5. Data Requirements

### 5.1 `customer_agent_records` — the only agent-visible table

| Field | Type | Null | Constraint / note |
|---|---|---|---|
| `customer_id` | TEXT | No | PK part 1 |
| `account_id` | TEXT | No | PK part 2; indexed; every query filters on it |
| `customer_name` | TEXT | Yes | PII — never leaves the repository layer into a prompt |
| `email` | TEXT | Yes | PII — rendering only |
| `phone` | TEXT | Yes | PII — rendering only |
| `customer_status` | TEXT | No | ACTIVE / INACTIVE / CHURNED |
| `registration_date` | DATE | No | Tenure basis |
| `last_activity_at` | TIMESTAMP | Yes | Recency signal |
| `last_login_at` | TIMESTAMP | Yes | Login-lapse signal |
| `last_purchase_at` | TIMESTAMP | Yes | Purchase-gap signal |
| `total_orders` | INTEGER | No | Default 0; `>= 0` |
| `total_spend` | REAL | No | Default 0; `>= 0` |
| `average_order_value` | REAL | Yes | Consistency-checked against spend/orders |
| `purchase_frequency_days` | REAL | Yes | Observed mean interval; falls back to tenure/orders |
| `email_open_rate` | REAL | Yes | 0–1, **trailing 90 days** |
| `email_click_rate` | REAL | Yes | 0–1, `<= email_open_rate`, trailing 90 days |
| `sms_response_rate` | REAL | Yes | 0–1, trailing 90 days |
| `email_open_rate_prev_90d` | REAL | Yes | 0–1, the 90 days before the current window — trend basis |
| `sms_response_rate_prev_90d` | REAL | Yes | 0–1, prior window |
| `orders_last_90d` | INTEGER | No | Default 0, current window |
| `orders_prev_90d` | INTEGER | No | Default 0, prior window |
| `cart_abandonment_count_90d` | INTEGER | No | Default 0, **trailing 90 days**, not lifetime |
| `support_issue_count_90d` | INTEGER | No | Default 0, trailing 90 days |
| `preferred_channel` | TEXT | Yes | EMAIL / SMS / NONE |
| `email_consent` | INTEGER | No | 0/1 — send-time gate |
| `sms_consent` | INTEGER | No | 0/1 — send-time gate |
| `last_purchase_category` | TEXT | Yes | Rendering placeholder source |
| `data_as_of` | TIMESTAMP | No | Freshness |

**Two fixed windows**, both anchored on `data_as_of`: the **current** window is the
trailing 90 days; the **prior** window is the 90 days before that. Engagement rates
and the three counters are windowed. `total_orders` and `total_spend` stay lifetime,
because value tiering needs the whole history.

**Derived, never stored:** `days_since_activity`, `days_since_login`,
`days_since_purchase`, `churn_score`, `churn_risk_level`, `reason_codes`,
`value_tier`. Computed on read so they can never be stale.

### 5.2 App DB entities (agent-unreachable)

`campaigns`, `campaign_segments`, `campaign_targets`, `message_variants`,
`campaign_approvals`, `send_log`, `engagement_events`, `suppressions`, `agent_runs`.
`campaign_targets` holds the frozen audience that the approval hash covers.
Field-level definitions are in the TRD.

### 5.3 Data quality rules enforced on read

| ID | Rule | On violation |
|---|---|---|
| DQ-01 | Rates lie within 0–1 | Clamp, log a warning, count in a metric |
| DQ-02 | `email_click_rate <= email_open_rate` | Clamp click to open |
| DQ-03 | Timestamps are not in the future | Treat as now; flag record |
| DQ-04 | `total_orders > 0` implies `last_purchase_at` is present, and vice versa | Exclude the purchase-gap signal for that customer and renormalise weights |
| DQ-04a | `orders_last_90d <= total_orders` and `orders_prev_90d <= total_orders` | Clamp to `total_orders`; flag record |
| DQ-04b | Prior-window rate present but current-window rate null | Treat engagement as unusable; the signal is excluded, not scored as a total decline |
| DQ-05 | `data_as_of` older than the freshness window | Mark `stale=true` in tool output |
| DQ-06 | A customer with no contact field for the selected channel | Skip at send with reason `NO_CONTACT` |

---

## 6. Validation Requirements

| ID | Layer | Rule | On failure |
|---|---|---|---|
| VR-01 | Request | FastAPI/Pydantic validates every request body and query parameter | 422 with field errors |
| VR-02 | Scope | The resolved scope is non-empty and contains the target account | 403 |
| VR-03 | Tool input | Enum values, and `limit` within `1..50` | Structured tool error to the model |
| VR-04 | LLM output | Parses into the stage's Pydantic schema | One retry, then stage failure |
| VR-05 | Business rules | Playbook exists; segments non-empty; channels valid; variant count met; offer type allowed for the playbook | Campaign `FAILED` with rule ids |
| VR-06 | Policy | Discount within the tier cap; SMS length; unsubscribe footer present; no banned phrases; CTA key allow-listed | Campaign `FAILED` with rule ids |
| VR-07 | Content safety | No email address, phone number, order number, `customer_id`, URL outside the configured allowlist, or name-shaped literal in generated templates | Campaign `FAILED` |
| VR-08 | Placeholders | Every placeholder is on the allowlist | Campaign `FAILED` |
| VR-09 | Rendering | Every placeholder resolves for the customer | Skip that customer, record reason |
| VR-10 | Approval binding | The content-and-audience hash at send equals the approved hash | Abort send, campaign `FAILED` |
| VR-11 | Configuration | On startup, playbooks, policy and scoring config parse and are internally consistent | Service refuses to start |

---

## 7. Authentication

| ID | Priority | Requirement | Verify |
|---|---|---|---|
| AU-01 | MUST | Every endpoint except `/health` requires a valid `X-API-Key`. | T |
| AU-02 | MUST | An absent or unknown key returns 401 with no detail about why. | T |
| AU-03 | MUST | Keys are loaded from environment/configuration, never committed. `.env.example` documents them with placeholder values. | I |
| AU-04 | MUST | Key comparison is constant-time. | T |
| AU-05 | MUST | Keys are never logged, traced or returned. | T |
| AU-06 | SHOULD | The key's identity is attached to the request context and recorded on approvals. | T |
| AU-07 | MAY | Post-MVP: replace with OIDC/JWT without changing the scope-resolution interface. | — |

## 8. Authorization

| ID | Priority | Requirement | Verify |
|---|---|---|---|
| AZ-01 | MUST | Scope is resolved once per request from the key and stored in an immutable request context. | T |
| AZ-02 | MUST | Every repository method requires an explicit `account_id`; there is no default and no "all accounts" code path outside the seed script. | T, I |
| AZ-03 | MUST | The model cannot influence scope: no tool parameter, prompt field or output field feeds account selection. | T |
| AZ-04 | MUST | Approval and rejection require the `approver` role. | T |
| AZ-05 | MUST | Accessing a campaign belonging to another account returns 404, not 403 with details, to avoid confirming existence. | T |
| AZ-06 | MUST | Prompt-injection attempts to widen scope have no effect, because scope is bound before the model runs. | T |

---

## 9. Error Handling Requirements

| ID | Condition | Required behaviour |
|---|---|---|
| EH-01 | LLM transient failure (timeout, 429, 5xx) | Retry with exponential backoff and jitter up to the configured limit; then fail the stage with `stage`, `attempts` and `last_error` |
| EH-02 | LLM returns unparsable output | One re-ask with the schema error appended; then fail the stage |
| EH-03 | Token budget exceeded | Abort the run; campaign `FAILED` with `BUDGET_EXCEEDED`; usage recorded |
| EH-04 | Agent DB unavailable or not read-only | Refuse to serve agent endpoints; `/health` reports degraded |
| EH-05 | App DB write failure | Roll back the transaction; campaign state unchanged; 503 to the caller |
| EH-06 | Serper failure or timeout | Log, emit an error metric, continue without external context |
| EH-07 | Provider send failure | Record `FAILED` with the provider error; retry per policy; never record as sent |
| EH-08 | Partial send batch failure | Campaign completes with per-recipient statuses; aggregate counts reflect reality |
| EH-09 | Invalid state transition | 409 with current and requested state; no side effect |
| EH-10 | Unhandled exception | 500 with a correlation id only; the full detail goes to logs/traces, never to the client |
| EH-11 | Configuration invalid at startup | Fail fast with the offending key; do not start |
| EH-12 | Repeated provider failures | Circuit-breaker opens after the configured consecutive-failure count; further sends fail fast until the cooldown elapses |

---

## 10. Edge Cases

| ID | Case | Required behaviour |
|---|---|---|
| EC-01 | Account has zero customers | Campaign creation returns a clear empty-result response; no LLM call is made |
| EC-02 | Account has zero at-risk customers at the requested level | Same as EC-01, stating the filter that matched nothing |
| EC-03 | Customer has never purchased (`total_orders = 0`) | Purchase-gap signal excluded, remaining weights renormalised; may still qualify via dormancy/login lapse |
| EC-04 | Customer registered today | Tenure-based expectations are not applied; risk defaults to LOW unless explicit signals exist |
| EC-05 | All engagement rates null | Engagement signal excluded; if fewer than `min_signals_required` signals remain, risk is `UNKNOWN`, score is null, and the customer is excluded from campaign targeting but still counted and reported |
| EC-06 | A segment predicate matches zero customers | Segment dropped with a recorded reason; the campaign proceeds if any segment survives |
| EC-07 | Every segment is empty | Campaign `FAILED` with `NO_TARGETABLE_CUSTOMERS` |
| EC-08 | Customer matches two segment predicates | Assigned to the highest-priority segment only; a customer receives at most one campaign treatment |
| EC-09 | Customer lacks consent for the chosen channel | Skipped for that channel; if the campaign has a secondary channel with consent, that one is used |
| EC-10 | Customer unsubscribed between approval and send | Skipped at send with reason `SUPPRESSED` |
| EC-11 | Approval arrives after cancellation | 409; cancellation wins |
| EC-12 | Two concurrent approvals for one campaign | Exactly one succeeds; the other gets 409 (guarded by a conditional state update) |
| EC-13 | Campaign has no engagement events yet | Metrics return zeros with `null` rates and `INSUFFICIENT_DATA`; optimization declines to recommend |
| EC-14 | A/B variants tie exactly | Verdict `NO_DIFFERENCE`; no winner named |
| EC-15 | One variant received zero sends | Excluded from comparison, reported explicitly |
| EC-16 | Prompt injection embedded in a customer text field | Impossible to reach the prompt: free-text customer fields are not included in tool output |
| EC-17 | Model requests a tool that does not exist | Structured tool error; the run continues; the attempt is counted in a metric |
| EC-18 | Model loops on tool calls in `/agent/query` | Hard iteration cap; on hitting it, return the best grounded answer with a truncation flag |
| EC-19 | Very large account (100k+ customers) | Tools return aggregates plus a capped sample; the prompt size is independent of account size |
| EC-20 | Duplicate `customer_id` across accounts | Legal — uniqueness is per `(account_id, customer_id)` |
| EC-21 | Clock skew makes `days_since_*` negative | Clamped to 0 and flagged (DQ-03) |
| EC-22 | Grafana Cloud OTLP endpoint unreachable | Telemetry is dropped after retry; the application continues serving |
| EC-23 | Account where most customers have never purchased | Non-purchasers go straight to `LOW_VALUE`; percentiles are computed over purchasers only, so no never-purchaser can be ranked VIP |
| EC-24 | Account with fewer than `min_purchasers_for_tiering` purchasers | All purchasers are `STANDARD`; the campaign response states that tiering was suppressed |
| EC-25 | Customer has prior-window engagement but none in the current window | Engagement signal excluded rather than scored as a 100% decline — absence of data is not evidence of collapse |
| EC-26 | Customer is targeted on both EMAIL and SMS | Two messages, which exhausts the default frequency cap of 2 per 14 days; both count against it |
| EC-27 | Campaign approved, then a target unsubscribes before send | The frozen list is unchanged and the hash still matches; that recipient is skipped at send with reason `SUPPRESSED` |
| EC-28 | Someone edits `campaign_targets` between approval and send | Hash recomputation fails and the entire send aborts — the audience is inside the hash |
| EC-29 | `revise` called on a campaign that is not `REJECTED` or `FAILED` | 409; only terminal-failed campaigns can be revised |
| EC-30 | `simulate-events` called when `ENV != dev` | 404, as though the route did not exist |

---

## 11. Security Requirements

| ID | Priority | Requirement | Verify |
|---|---|---|---|
| SEC-01 | MUST | The agent's database connection is read-only; INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, ATTACH and PRAGMA writes all fail. | T |
| SEC-02 | MUST | The agent's database file contains exactly one table. | T |
| SEC-03 | MUST | No code path gives the model a raw SQL string, a table name or a column list. | T, I |
| SEC-04 | MUST | All agent-facing SQL is parameterised and confined to a single repository module. | T, I |
| SEC-05 | MUST | Every agent-facing query includes `WHERE account_id = ?` with a scope value that did not originate from the model. | T |
| SEC-06 | MUST | No LLM request payload contains `customer_name`, `email` or `phone`. | T |
| SEC-07 | MUST | Logs contain no customer PII and no full customer records; identifiers are logged as ids only. | T |
| SEC-08 | MUST | Secrets come from environment variables; no key appears in source, logs, traces or responses. | T, I |
| SEC-09 | MUST | The model cannot trigger a send, an approval or a state transition; those are orchestrator- and API-only capabilities. The agent package holds no app-DB connection — the orchestrator persists `agent_runs` from the usage record each stage returns. | T, I |
| SEC-10 | MUST | Approval is bound to a hash covering content, offer and the frozen audience, and re-verified at send. | T |
| SEC-11 | MUST | Rendering escapes customer values appropriate to the channel, so a value cannot inject markup or break the message body. | T |
| SEC-12 | MUST | Serper receives only the model's generated market-context query; no customer data is transmitted externally. | T |
| SEC-13 | SHOULD | Rate limiting on `/agent/query` and `/campaigns` bounds cost and abuse. | T |
| SEC-14 | SHOULD | Dependencies are pinned and scanned for known vulnerabilities. | I |
| SEC-15 | MUST | An adversarial prompt suite (scope escape, PII extraction, write attempts, policy bypass, injected instructions) runs in CI, and every case must fail to achieve its objective. | T |

---

## 12. Non-Functional Requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-01 | Performance | Non-agent endpoints p95 under 500 ms at 5,000 customers per account |
| NFR-02 | Performance | Campaign generation completes in under 90 s for a 5,000-customer account |
| NFR-03 | Performance | `/agent/query` p95 under 15 s |
| NFR-04 | Cost | Under 60,000 tokens per campaign generation, enforced by a hard budget cap. At `gpt-5-nano` rates that is roughly $0.008 per campaign |
| NFR-05 | Scalability | Prompt size is independent of account size; scoring is O(n) over account rows with n up to 100k |
| NFR-06 | Portability | Swapping SQLite for PostgreSQL touches only the connection and repository modules |
| NFR-07 | Observability | Traces, metrics and logs export to Grafana Cloud over OTLP with correlated `trace_id` |
| NFR-08 | Reliability | Telemetry export failure never affects request handling |
| NFR-09 | Maintainability | Single agent, single tool module, single scoped-query helper, single policy module |
| NFR-10 | Testability | Deterministic seeds for data generation, variant assignment and event simulation |
| NFR-11 | Reproducibility | Campaigns record model id, prompt version, config version and token usage |
| NFR-12 | Compliance posture | Consent flags and suppression are honoured mechanically; opt-out content is mandatory |

---

## 13. Requirement Verification Matrix (traceability summary)

| Area | Requirements | Verified by |
|---|---|---|
| One table, read-only | FR-01, SEC-01, SEC-02, SEC-03, SEC-04 | `tests/security/test_db_boundary.py` |
| Account isolation | FR-13, FR-66, AZ-01…AZ-06, SEC-05 | `tests/security/test_account_isolation.py` |
| No PII to the model | FR-14, FR-25, SEC-06, SEC-07 | `tests/security/test_pii_boundary.py` |
| Policy cannot be bypassed | FR-37, FR-38, VR-06, VR-07 | `tests/test_policy_engine.py` |
| Approval integrity | FR-41…FR-46, VR-10, SEC-10 | `tests/test_approval.py` |
| Deterministic maths | FR-04, FR-09, FR-56, FR-59, FR-11 | `tests/test_scoring.py`, `tests/test_analytics.py` |
| Adversarial resilience | SEC-15, EC-16, EC-17, EC-18 | `tests/security/test_prompt_injection.py` |
| Loop completeness | FR-63…FR-65, AC-1…AC-9 | `tests/test_e2e_loop.py` |
| Trend scoring | FR-04a, FR-04b, FR-04d, EC-25 | `tests/test_scoring.py` |
| Tiering edge cases | FR-09a, FR-09b, EC-23, EC-24 | `tests/test_value_tiering.py` |
| Audience-bound approval | FR-42a, EC-27, EC-28 | `tests/test_approval.py` |
| Suppression lifecycle | FR-40a, EC-10 | `tests/test_communication.py` |
| Attribution & ROI basis | FR-56a…FR-56d | `tests/test_analytics.py` |
