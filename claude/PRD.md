# Product Requirements Document
## Texting Agent

| Field | Value |
|---|---|
| Document | PRD |
| Version | 1.0 |
| Date | 2026-08-29 |
| Status | Approved for build |
| Related | [SRS.md](SRS.md) · [TRD.md](TRD.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) · [UIUX.md](UIUX.md) |

---

## 1. Problem Statement

Businesses lose revenue to customers who quietly stop buying. The failure is rarely
detection alone — most companies already have the behavioural data sitting in a
database. The failure is the **gap between detection and action**:

1. **Detection is late.** Churn is noticed at reporting time, weeks after the
   behavioural signal appeared.
2. **Analysis does not scale.** Understanding *why* a specific cohort is
   disengaging requires an analyst to read behaviour patterns customer by customer.
3. **Action is generic.** The usual response is one blanket discount email to
   everybody, which erodes margin on customers who were never going to leave and
   under-serves high-value customers who needed a different intervention.
4. **The loop never closes.** Campaign results are rarely fed back into the next
   campaign's strategy.

The result is a retention function that is slow, undifferentiated, margin-destructive,
and does not learn.

### Why an agent, and not a dashboard

A dashboard tells you *who*. It cannot tell you *why*, choose *what to do*, write
*the message*, or *learn from the outcome*. Those four steps are judgement work, and
they are exactly what an LLM is good at — provided the judgement is fenced by
deterministic code for everything that must be correct rather than plausible
(who is at risk, what an account is allowed to see, what a discount may be, what a
conversion rate is).

### The problem this product does *not* solve

It does not predict churn better than a trained ML model would. v1 ships a
transparent, configurable **heuristic risk ranking**, not a calibrated probability.
That is a deliberate trade — see [Assumptions](#8-assumptions) and
[Risks](#9-risks).

---

## 2. Target Users

| # | User | Role in the system | Primary need |
|---|---|---|---|
| U1 | **Retention / Lifecycle Marketing Manager** | Primary operator. Requests campaigns, reviews generated messages, approves or rejects. | Go from "which customers are slipping away" to an approved, differentiated campaign in minutes instead of a sprint. |
| U2 | **Growth / Marketing Analyst** | Consumer of intelligence. Uses `/agent/query` and metrics endpoints. | Ask questions of the customer base in plain language and get grounded, auditable answers. |
| U3 | **Marketing Ops / Approver** | Governance. Holds the approval role. | Confidence that nothing reaches a customer without a human seeing the exact content, and that policy caps are technically impossible to exceed. |
| U4 | **Platform / Backend Engineer** | Operator of the system. | A service that is observable, testable, and whose data boundaries are enforced by code, not by prompt wording. |
| U5 | **Security / Compliance Reviewer** | Auditor. | Provable answers to: what data can the model reach, can it write, can it cross accounts, can it leak PII. |

**Non-users in v1:** end customers (they receive messages but never touch the system),
data scientists (no model-training surface), and self-serve external tenants
(multi-account is enforced, but onboarding is manual).

---

## 3. Goals

### 3.1 Product goals

| ID | Goal | How it is judged |
|---|---|---|
| G1 | Surface at-risk customers automatically, with grounded reasons | Every candidate carries machine-generated reason codes traceable to specific fields |
| G2 | Differentiate the response by risk **and** value | Distinct playbooks and offers per segment; no single blanket campaign |
| G3 | Produce ready-to-send email and SMS content | The approver reviews finished content, not a brief |
| G4 | Make a human the last gate before any send | No message leaves the system without a recorded human approval bound to that exact content |
| G5 | Close the loop | Campaign results are measured deterministically and fed back as the input to the next campaign's strategy |

### 3.2 Engineering goals

| ID | Goal | How it is judged |
|---|---|---|
| G6 | The model can reach exactly one table, read-only | Enforced by physical database separation and a read-only connection, verified by tests |
| G7 | The model cannot cross account boundaries | `account_id` is never a model-supplied parameter; every query is scoped by the application |
| G8 | The model cannot fabricate or leak customer PII | Customer contact fields never enter a prompt; the model writes templates, code fills in values |
| G9 | The model cannot exceed business policy | Offers, message length, footers, forbidden literals and frequency caps are validated deterministically after generation |
| G10 | Every meaningful operation is traceable | OpenTelemetry spans from HTTP request through agent stage, tool call, database query and send |

### 3.3 Explicit non-goals

- Beating a trained ML churn model on accuracy.
- Autonomous sending without human approval.
- A customer-facing UI (a UI/UX document will be supplied separately).
- Real-time / streaming churn detection.

---

## 4. Core Features

| ID | Feature | Description | MVP |
|---|---|---|---|
| F1 | **Churn detection** | Deterministic, config-driven scoring over behavioural fields producing a 0–1 risk score, a risk level, and ranked reason codes with evidence. Includes **trend** signals — engagement change and 90-day order change — not just point-in-time levels. | Yes |
| F2 | **Value tiering** | Percentile-based value tier per account over purchasers (VIP / HIGH_VALUE / STANDARD / LOW_VALUE); non-purchasers go straight to LOW_VALUE. No hard-coded currency thresholds, and value never feeds the risk score. | Yes |
| F3 | **Behavioural interpretation** | The agent explains *why* a cohort is at risk, grounded strictly in the reason codes and aggregates it was given. | Yes |
| F4 | **Segmentation** | The agent proposes segment *definitions* as structured predicates; deterministic code *assigns* customers to them. | Yes |
| F5 | **Retention playbooks** | A configurable playbook library (VIP_REACTIVATION, PRICE_SENSITIVE, CART_ABANDONMENT, DORMANT, SUPPORT_RECOVERY). The agent selects; it does not invent business policy. | Yes |
| F6 | **Channel selection** | Per-segment EMAIL / SMS / EMAIL+SMS decision driven by that segment's observed channel engagement. | Yes |
| F7 | **Message generation** | Per-segment email and SMS **templates** with allow-listed placeholders, plus A/B variants. | Yes |
| F8 | **Deterministic rendering** | Code substitutes real customer values into templates at send time. An unknown placeholder is a hard failure. | Yes |
| F9 | **Validation pipeline** | Pydantic schema → business rules → policy engine. Any failure blocks the campaign. | Yes |
| F10 | **Human approval** | Campaigns halt at `AWAITING_APPROVAL`. Approval is bound to a hash covering the content **and the frozen recipient list**; drift in either after approval blocks the send. | Yes |
| F11 | **Communication** | Mock email and SMS providers behind stable adapter interfaces, with suppression and frequency caps enforced at send time. | Yes |
| F12 | **Analytics** | Python-computed delivery, open, click, conversion and unsubscribe rates, revenue within an attribution window, gross attributed ROI including LLM cost, and reactivation rate over genuinely lapsed customers. The model never computes arithmetic. | Yes |
| F13 | **A/B testing with a significance gate** | Two-proportion z-test plus a minimum-sample gate. Below the gate the verdict is `INSUFFICIENT_DATA` and the agent may not declare a winner. | Yes |
| F14 | **Closed-loop optimization** | The agent reads measured results and produces the next campaign's recommendation. | Yes |
| F15 | **Natural-language intelligence queries** | `POST /agent/query` over the scoped, read-only tools. | Yes |
| F16 | **External research (Serper.dev)** | One optional `search_web()` tool for market context. Off by default. Never a source of customer facts. Failure is non-fatal. | Yes (flag-off) |
| F17 | **Observability** | OpenTelemetry traces, metrics and logs exported directly to **Grafana Cloud** over OTLP. No self-hosted Grafana components. | Yes |

---

## 5. MVP Definition

The MVP is the **complete loop on synthetic data with mock providers**. Every stage
of the intelligence loop must be real; only the outbound carriers are simulated.

### In scope

```
Seed synthetic customers (3 accounts x 5,000, plus a tiny fourth account for the small-sample edge cases)
   |  deterministic scoring + value tiering
Churn candidates with reason codes
   |  agent: analyse -> segment -> plan -> generate
Campaign plan + email/SMS template variants
   |  Pydantic -> business rules -> policy engine
Validated campaign
   |  human approval (bound to a content + audience hash)
Approved campaign
   |  deterministic render -> suppression/frequency check -> mock send
Delivery + simulated engagement events
   |  Python analytics + significance testing
Measured results
   |  agent: optimize
Recommendation for the next campaign
```

Plus: FastAPI service, API-key auth with account scoping, the two-database security
boundary, OTel to Grafana Cloud, and a security test suite that proves the boundaries.

### Out of MVP (deferred, not cancelled)

| Item | Why deferred |
|---|---|
| Real email/SMS providers + webhooks | Adds credentials, deliverability and compliance surface with no new architecture. The adapter interface is designed for it. |
| PostgreSQL | SQLite is sufficient at target scale; the repository layer is written to migrate without rewriting callers. |
| Trained ML churn model | Heuristic scoring is transparent and tunable; a model can replace the scoring service behind the same interface. |
| WhatsApp / push channels | The channel enum and adapter registry already accommodate them. |
| Campaign scheduling, pause, quiet hours and follow-up messages | All four need a background worker to defer sends to. v1 sends synchronously on approval, so there is no running state to pause and nowhere to defer to. `cancel` and `revise` are supported instead. |
| Multi-user RBAC beyond two roles | Two roles (`operator`, `approver`) cover the approval control. |
| Operator console UI | Design system and build plan now exist in [UIUX.md](UIUX.md). The marketing landing page (U0–U3) proceeds in parallel; the console (U4+) waits for backend M9. |

---

## 6. User Stories

### 6.1 MVP user stories

| ID | As a… | I want to… | So that… | Acceptance |
|---|---|---|---|---|
| US-01 | Retention Manager | ask which customers are likely to churn | I can size the problem before committing budget | Returns totals by risk level and value tier, plus dominant reason codes, scoped to my account only |
| US-02 | Retention Manager | see *why* a cohort is at risk | I can trust the recommendation | Every stated reason maps to a reason code derived from a real field; no unsupported claims |
| US-03 | Retention Manager | create a retention campaign for high-risk customers | I get differentiated strategies rather than one blanket blast | At least 2 distinct segments, each with its own playbook, offer and channel decision |
| US-04 | Retention Manager | read the exact email and SMS content before it sends | I am accountable for what customers receive | `GET /campaigns/{id}/messages` returns every variant plus a rendered preview against a sample customer |
| US-05 | Approver | approve or reject a campaign | nothing reaches a customer unreviewed | State moves only `AWAITING_APPROVAL -> APPROVED`/`REJECTED`; a second approve is rejected idempotently |
| US-06 | Approver | be certain the approved content **and audience** are what sends | neither can drift after sign-off | Send verifies the stored content-and-audience hash; a mismatch fails the send and does not silently proceed |
| US-07 | Analyst | see how a campaign performed | I can report on it | Deterministic metrics: delivered, opened, clicked, converted, unsubscribed, revenue, ROI, reactivation rate |
| US-08 | Analyst | know which A/B variant won | I can pick the next experiment | A winner is declared only above the sample gate and below p<0.05; otherwise `INSUFFICIENT_DATA` |
| US-09 | Retention Manager | get a recommendation for the next campaign | the programme improves each cycle | The recommendation cites the measured metrics it is based on |
| US-10 | Security Reviewer | prove the agent cannot write to the database | I can sign off the deployment | An automated test asserts INSERT/UPDATE/DELETE/DDL on the agent connection all fail |
| US-11 | Security Reviewer | prove the agent cannot see another account's customers | multi-tenancy is safe | An automated test asserts account A's request never returns account B rows, including under prompt-injection attempts via `/agent/query` |
| US-12 | Engineer | trace a campaign end to end | I can debug and cost it | A single Grafana Cloud trace spans request, agent stages, tool calls, DB queries, validation and sends |

### 6.2 Post-MVP user stories

| ID | Story |
|---|---|
| PS-01 | As a Retention Manager, I want campaigns to send through a real provider so that live customers are reached. |
| PS-02 | As an Analyst, I want real engagement webhooks so that metrics reflect actual behaviour rather than simulation. |
| PS-03 | As a Retention Manager, I want to schedule a campaign for a future window and pause a running one. |
| PS-04 | As a Data Scientist, I want to swap the heuristic score for a trained model without touching the agent. |
| PS-05 | As a Retention Manager, I want a holdout control group so that incremental lift is measurable, not just gross attributed conversion. |
| PS-09 | As a Retention Manager, I want quiet hours and multi-touch follow-up cadences, once a background worker exists to schedule them. |
| PS-06 | As an Ops user, I want WhatsApp and push as additional channels. |
| PS-07 | As a Platform Engineer, I want PostgreSQL with a genuine `GRANT SELECT`-only role for the agent. |
| PS-08 | As a Retention Manager, I want a web console for the approval queue — planned as UIUX.md milestone U4. |

---

## 7. Success Metrics

### 7.1 Product metrics (measured by the system; on simulated data in v1)

| Metric | Definition | v1 target |
|---|---|---|
| Candidate coverage | Share of the account's customers assigned a risk level, or explicitly marked `UNKNOWN` with the reason | 100% |
| Segment differentiation | Distinct playbook/offer combinations per campaign | At least 2, and no two segments in different value tiers receive an identical offer |
| Reactivation rate | Converted / contacted, over targets that were genuinely lapsed at send time | Reported; no numeric target on synthetic data |
| Gross attributed ROI | (revenue attributed inside the window − campaign cost incl. LLM spend) / campaign cost | Reported with an explicit "not incremental" basis; must be computable end to end |
| Unsubscribe rate | Unsubscribed / delivered | Reported and alertable. **No numeric target** — on simulated data this metric reflects the simulator's configured rate, not the product |

### 7.2 Operating metrics

| Metric | v1 target |
|---|---|
| Time from "create campaign" to `AWAITING_APPROVAL` | Under 90 seconds for a 5,000-customer account |
| `/agent/query` p95 latency | Under 15 seconds |
| Non-agent API p95 latency | Under 500 ms |
| LLM token cost per campaign generation | Under 60,000 total tokens, enforced by a per-run budget cap — about $0.008 per campaign on `gpt-5-nano` |
| Agent stage error rate | Under 2%, with automatic retry on transient failures |

### 7.3 Security metrics — pass/fail, not targets

| Check | Requirement |
|---|---|
| Write attempts from the agent connection | 100% fail |
| Cross-account leakage in any endpoint, including adversarial `/agent/query` prompts | 0 occurrences |
| Customer PII (name, email, phone) present in any LLM prompt or completion | 0 occurrences |
| Sends without a matching content-and-audience hash | 0 occurrences |
| Offers exceeding the policy cap reaching `VALIDATED` | 0 occurrences |

---

## 8. Assumptions

| ID | Assumption | If wrong |
|---|---|---|
| A1 | `customer_agent_records` is populated by an upstream ETL the agent does not own. In v1 a seed script plays that role. | Add an ingestion component; no agent change. |
| A2 | Behavioural fields (activity, purchase and engagement-rate columns) are accurate and refreshed at least daily. | Scores go stale; stale records are already excluded from targeting and counted. |
| A2a | The upstream feed can supply prior-period columns for the 90 days before the current window. Without them the system degrades to level-only scoring and emits `LOW_ENGAGEMENT` rather than `ENGAGEMENT_DECLINE`. | Trend signals drop out; scoring renormalises over what remains. |
| A3 | A heuristic weighted score over level **and trend** signals is adequate to *rank* risk for v1. It is not a calibrated probability and must not be presented as one. | Replace the scoring service with a trained model behind the same interface. |
| A4 | Per-segment templates deliver enough personalization. | Increase segment granularity before considering per-customer generation. |
| A5 | Approval volume is human-reviewable — tens of variants, not hundreds of messages. | Add bulk-approval tooling; do not remove the gate. |
| A6 | Target scale is at most ~100k customers per account, so SQLite suffices. | Migrate to PostgreSQL via the repository layer. |
| A7 | A static API-key-to-account mapping is acceptable auth for v1. | Move to OIDC; the scoping mechanism is unchanged. |
| A8 | Grafana Cloud is the observability backend and accepts OTLP directly. | Insert a collector; app-side instrumentation is unchanged. |
| A9 | Simulated engagement events are acceptable for demonstrating the closed loop. | Integrate real webhooks (PS-02). |

---

## 9. Risks

| ID | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| R1 | **Model fabricates customer facts** | High | Medium | The model never sees PII and emits templates only; code renders values. Every claim must map to a supplied reason code; validation rejects unrecognised placeholders. |
| R2 | **Cross-account data leakage** | Critical | Low | `account_id` is never a model parameter; a single scoped-query helper is the only SQL path; automated isolation tests including prompt-injection cases. |
| R3 | **Agent writes to the database** | Critical | Low | Physically separate database file opened read-only with `PRAGMA query_only`; no SQL tool exposed; automated write-attempt tests. |
| R4 | **Prompt injection via customer data** | High | Medium | Free-text customer fields are excluded from prompts. Tool outputs are structured Pydantic objects, not raw text. The agent has no write, send or approve capability, so a successful injection has nothing to actuate. |
| R5 | **Policy-violating offer reaches a customer** | High | Low | Deterministic policy engine after generation; a violation fails the campaign rather than being silently auto-corrected. |
| R6 | **Content or audience drift between approval and send** | High | Low | Approval stores a hash over content, offer and the frozen recipient list; send re-verifies it. Send-time gates may only remove recipients. |
| R7 | **False positives waste margin on discounts** | Medium | High | Value-tier-aware playbooks; the cheapest intervention is the default for LOW_VALUE; discount caps in policy; ROI reported per campaign. |
| R8 | **Heuristic score mistaken for a probability** | Medium | High | Documented as a ranking in the API response, the docs and the agent's instructions. |
| R14 | **Prior-period columns unavailable or wrong in a real feed** | Medium | Medium | Trend signals are dropped per-customer when their inputs are missing, weights renormalise, and the reason code degrades to `LOW_ENGAGEMENT` — the system stays honest about what it measured. |
| R15 | **`gpt-5-nano` too weak for nested structured output or sendable copy** | Medium | Medium | Per-stage model env vars: bump only the failing stage. Parse-retry path already exists, and M6's definition of done requires a human to judge the copy sendable. |
| R16 | **Gross attributed ROI read as incremental** | Medium | High | Metric is named `gross_attributed_roi`, carries a basis note in every response, and holdout groups are an explicit post-MVP item. |
| R9 | **Declaring an A/B winner on noise** | Medium | High | Minimum-sample gate plus z-test; the agent is contractually forbidden to declare a winner when the verdict is `INSUFFICIENT_DATA`. |
| R10 | **LLM cost/latency blowout on large accounts** | Medium | Medium | The agent receives aggregates and capped samples (at most 50 rows), never full customer sets; per-run token budget cap; a small model for classification stages. |
| R11 | **Serper outage blocks a campaign** | Low | Medium | Research is optional, cached, timeout-bounded, and its failure is logged and skipped. |
| R12 | **Over-engineering observability** | Medium | Medium | Grafana Cloud over OTLP directly — no Alloy, Tempo or Prometheus to run or maintain. |
| R13 | **Scope creep from the full 19-phase spec** | Medium | High | The MVP is fixed at the loop above; deferred items are listed explicitly and are not started until the loop is green. |

---

## 10. Out of Scope

Not built in this project, in any phase:

- Customer-facing web or mobile applications. (The marketing landing page and the internal operator console are in scope — see [UIUX.md](UIUX.md).)
- Payment processing, order management or discount-code redemption systems.
- CRM or CDP replacement; the system reads a prepared feature table only.
- Data ingestion / ETL pipelines from source systems.
- Autonomous mass sending with no human in the loop.
- General-purpose SQL access for the model, in any form.
- Multiple LLM agents, or agent-to-agent delegation.
- Model training, fine-tuning or evaluation infrastructure.
- Deliverability and reputation management (SPF, DKIM, IP warm-up).
- Legal compliance certification (GDPR / CAN-SPAM / TCPA). Consent fields and
  suppression are respected mechanically; certification is not claimed.

---

## 11. Acceptance Criteria

The MVP is accepted when **all** of the following hold.

### Functional

- [ ] **AC-1** `POST /agent/query` with "show me customers likely to churn" returns totals by risk level and value tier for the caller's account only, with dominant reason codes.
- [ ] **AC-2** `POST /campaigns` produces a plan with at least 1 segment — and at least 2 when the candidate pool spans more than one value tier — each carrying a playbook, an offer within policy, a channel decision justified by that segment's channel engagement, and at least 2 message variants.
- [ ] **AC-3** `GET /campaigns/{id}/messages` returns every variant and a rendered preview for a sample customer with all placeholders resolved.
- [ ] **AC-4** A campaign whose generated offer exceeds the policy cap is rejected at validation, never reaches `AWAITING_APPROVAL`, and the response names the violated rule.
- [ ] **AC-5** `POST /campaigns/{id}/approve` moves `AWAITING_APPROVAL -> APPROVED`; a repeat call is rejected without side effects.
- [ ] **AC-6** Sending renders per-customer messages, skips suppressed and frequency-capped customers with a recorded reason, and dispatches through the mock adapters.
- [ ] **AC-7** `GET /campaigns/{id}/metrics` returns Python-computed delivery, open, click, conversion and unsubscribe rates, windowed revenue, gross attributed ROI with its basis note, and reactivation rate.
- [ ] **AC-8** `GET /campaigns/{id}/optimization` returns a recommendation citing the measured metrics; with insufficient samples it explicitly declines to name a winner.
- [ ] **AC-9** With `SERPER_ENABLED=false`, or with Serper failing, campaign generation still completes successfully.
- [ ] **AC-9a** `POST /campaigns` without `account_id` returns 400; with an out-of-scope `account_id` returns 403.
- [ ] **AC-9b** A customer with fewer than the required number of usable signals is reported as `UNKNOWN` and never appears in a campaign audience.
- [ ] **AC-9c** A rejected campaign can be revised into a new campaign that carries the rejection reason as input; the original stays `REJECTED`.
- [ ] **AC-9d** All eight demo requests run through the HTTP API with no manual script step.

### Security — all covered by automated tests

- [ ] **AC-10** Every write and DDL statement attempted on the agent database connection fails.
- [ ] **AC-11** The agent's database file contains exactly one table, `customer_agent_records`.
- [ ] **AC-12** A request authenticated for account A never returns account B data, including when the `/agent/query` prompt explicitly instructs the model to ignore its scope or name another account.
- [ ] **AC-13** No prompt sent to the LLM contains `customer_name`, `email` or `phone` — asserted by a test that inspects captured payloads.
- [ ] **AC-14** A send whose content-and-audience hash does not match the approved hash is blocked, including when only the recipient list was altered.
- [ ] **AC-15** No API key grants access to an account it is not mapped to; requests without a valid key are rejected with 401.

### Operational

- [ ] **AC-16** A campaign run produces a single connected trace in Grafana Cloud covering request, agent stages, tool calls, database queries, validation and sends.
- [ ] **AC-17** The business and technical metrics defined in the TRD are visible in Grafana Cloud.
- [ ] **AC-18** `uv sync` plus `uv run texting-agent` and a seed command yields a working system from a clean checkout, with `.env.example` documenting every variable. A Docker path exists as well.
- [ ] **AC-19** Logs are structured JSON carrying `trace_id`, `account_id` and `campaign_id`, and contain no customer PII.
- [ ] **AC-20** The full test suite passes, including the security suite, via a single documented command.
