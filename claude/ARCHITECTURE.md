# System Architecture Document
## Texting Agent

| Field | Value |
|---|---|
| Document | Architecture |
| Version | 1.0 |
| Date | 2026-08-29 |
| Related | [PRD.md](PRD.md) · [SRS.md](SRS.md) · [TRD.md](TRD.md) · [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) · [UIUX.md](UIUX.md) |

---

## 1. Architectural Principles

Five rules decide every design question in this system. When a later decision
conflicts with an earlier one, the earlier one wins.

| # | Principle | Consequence |
|---|---|---|
| **P1** | **The LLM reasons. Code decides.** | Anything that must be *correct* rather than *plausible* — who is at risk, which account, what a discount may be, what a conversion rate is — is deterministic code. |
| **P2** | **Security is structural, not textual.** | Every boundary has a mechanism behind it. A prompt instruction is at best a second layer, never the layer. |
| **P3** | **One agent.** | One class, one instruction set, one toolset. No sub-agents, no delegation. Complexity that would go into agent choreography goes into the deterministic orchestrator instead, where it can be tested. |
| **P4** | **The blast radius of a compromised model is nil.** | The model cannot write, send, approve, choose an account, or see a name. A fully successful prompt injection still actuates nothing. |
| **P5** | **Boring beats clever.** | No queue, no cache server, no agent framework, no self-hosted observability stack until something measurably requires it. |

---

## 2. System Context

```
        ┌──────────────┐        ┌──────────────┐        ┌──────────────┐
        │  Retention   │        │   Approver   │        │   Analyst    │
        │   Manager    │        │  (Mkt Ops)   │        │              │
        └──────┬───────┘        └──────┬───────┘        └──────┬───────┘
               │                       │                       │
               └───────────────┬───────┴───────────────────────┘
                               │  HTTPS + X-API-Key
                    ┌──────────▼──────────────────────────────┐
                    │   Texting Agent  (FastAPI)       │
                    └───┬───────────┬───────────┬─────────┬───┘
                        │           │           │         │
             ┌──────────▼──┐  ┌─────▼────┐ ┌────▼─────┐ ┌─▼──────────────┐
             │  OpenAI API │  │ Serper   │ │ Email /  │ │ Grafana Cloud  │
             │  (reasoning)│  │(optional)│ │ SMS mock │ │ (OTLP)         │
             └─────────────┘  └──────────┘ └──────────┘ └────────────────┘
                        │
             ┌──────────▼────────────────────────────┐
             │ SQLite: customer_agent.db   (read-only)│
             │ SQLite: app.db              (read-write)│
             └────────────────────────────────────────┘
```

**Upstream, out of scope:** whatever ETL populates `customer_agent_records`. In v1 a
seed script plays that role.

---

## 3. Component Architecture

```
┌───────────────────────────────────────────────────────────────────────────┐
│                              API LAYER                                    │
│  health.py    agent.py (/agent/query)    campaigns.py                     │
│  deps.py → authenticate → resolve scope → immutable RequestContext        │
└───────────────────────────────┬───────────────────────────────────────────┘
                                │ scope is fixed here and never widens
┌───────────────────────────────▼───────────────────────────────────────────┐
│                     ORCHESTRATOR  (deterministic state machine)           │
│  states.py · transitions.py · workflow.py                                 │
│  Owns: control flow, state, retries, what the agent is asked and when     │
└───┬──────────────────────────────────┬────────────────────────────────┬───┘
    │                                  │                                │
┌───▼──────────────────┐  ┌────────────▼─────────────┐  ┌───────────────▼───┐
│  DETERMINISTIC       │  │   TextingAgent    │  │   VALIDATION      │
│  SERVICES            │  │   (the only LLM caller)  │  │   PIPELINE        │
│                      │  │                          │  │                   │
│  scoring_service     │  │  analyze  · segment      │  │  pydantic         │
│  value_service       │  │  plan     · generate     │  │  business rules   │
│  segmentation_service│  │  optimize · query        │  │  policy engine    │
│  playbook_service    │  │                          │  │  content safety   │
│  rendering_service   │  │  ┌────────────────────┐  │  └───────┬───────────┘
│  analytics_service   │  │  │  ScopedToolset     │  │          │
│  communication_svc   │  │  │  account bound at  │  │  ┌───────▼───────────┐
│  policy_service      │  │  │  construction      │  │  │ HUMAN APPROVAL    │
└───┬──────────────────┘  │  └─────────┬──────────┘  │  │ content+audience  │
    │                     └────────────┼─────────────┘  └───────┬───────────┘
    │                                  │ read-only                     │
┌───▼──────────────────────────────────▼───────────┐  ┌───────────────▼───────┐
│ REPOSITORIES                                     │  │ INTEGRATIONS          │
│  customer_repo → customer_agent.db  (RO)         │  │  openai · serper      │
│  campaign_repo → app.db             (RW)         │  │  email/sms adapters   │
└──────────────────────────────────────────────────┘  └───────────────────────┘
                                │
                   ┌────────────▼──────────────┐
                   │ OBSERVABILITY (cross-cut) │
                   │ tracing · metrics · logs  │──► Grafana Cloud (OTLP)
                   └───────────────────────────┘
```

### Component responsibilities

| Component | Owns | Explicitly does not |
|---|---|---|
| **API layer** | HTTP contract, authentication, scope resolution, role checks | Business logic, LLM calls |
| **Orchestrator** | State machine, pipeline sequence, transition guards, retries | Reasoning, SQL, provider calls |
| **Agent** | All LLM reasoning, in five fixed stages plus one query loop | Deciding account, computing numbers, writing, sending, approving |
| **ScopedToolset** | The complete model-callable surface, account bound at construction | Accepting an account, a table, a column or SQL from the model |
| **Deterministic services** | Scoring, tiering, segment assignment, rendering, analytics, policy, sending | Anything requiring judgement |
| **Validation pipeline** | Schema, business rules, policy, content safety | Silently repairing bad output |
| **Repositories** | The only SQL in the system, always account-scoped | Being reachable from the agent package (app DB) |
| **Integrations** | External I/O with timeouts, retries, breakers | Being callable by the model |
| **Observability** | Traces, metrics, structured logs | Emitting customer PII |

---

## 4. Data Architecture

### 4.1 Storage topology

```
┌──────────────────────────────┐        ┌──────────────────────────────────┐
│   data/customer_agent.db     │        │        data/app.db               │
│                              │        │                                  │
│  customer_agent_records      │        │  campaigns                       │
│  ── the ONLY table in file   │        │  campaign_segments               │
│                              │        │  campaign_targets                │
│                              │        │  message_variants                │
│  Opened as:                  │        │  campaign_approvals              │
│    ?mode=ro&uri=true         │        │  send_log                        │
│    PRAGMA query_only=ON      │        │  engagement_events               │
│    PRAGMA trusted_schema=OFF │        │  suppressions                    │
│                              │        │  agent_runs                      │
└──────────────┬───────────────┘        └───────────────┬──────────────────┘
               │                                        │
       reachable from:                          reachable from:
       customer_repo only                       campaign_repo only
       (agent + services)                       (services only —
                                                 NOT the agent package)
```

Two files rather than two schemas is the whole security design in one decision. It
turns "the agent must not read other tables" from a promise into a physical fact:
the connection it holds is to a file where nothing else exists.

### 4.2 Data classification

| Class | Fields | Where it may travel |
|---|---|---|
| **PII** | `customer_name`, `email`, `phone` | Repository → rendering → provider adapter, and nowhere else. **Never** into a prompt, a log, a trace attribute, **or any API response** — `GET /campaigns/{id}/customers` returns ids and behaviour only. The one exception is the rendered preview in `GET /campaigns/{id}/messages`, which resolves a single sample customer so the approver sees real output. |
| **Behavioural** | activity/purchase timestamps, lifetime totals, windowed counters, current- and prior-window rates | Repository → scoring → `CustomerFacts` → prompt (allowed) |
| **Derived** | score, risk level, value tier, reason codes | Computed on read; never persisted; freely usable |
| **Operational** | campaign, variant, send, event rows | App DB; agent-unreachable |
| **Secret** | API keys, provider keys, OTLP token | Environment only |

### 4.3 Data flow — one campaign

```
customer_agent_records
   │ SELECT … WHERE account_id = ?          (scoped, parameterised)
   ▼
CustomerRecord[]  ── PII present, stays server-side ─────────────┐
   │ scoring_service + value_service                             │
   ▼                                                             │
ScoredCustomer[]  (score, risk, tier, reason evidence)           │
   │ strip PII                                                   │
   ▼                                                             │
CustomerFacts[] / aggregates ──► PROMPT ──► LLM                  │
                                     │                           │
                            templates + plans                    │
                                     │                           │
                            validation + policy                  │
                                     │                           │
                            human approval                       │
                                     │                           │
                            rendering ◄──────────────────────────┘
                                     │   (PII rejoins here, in code)
                                     ▼
                            provider adapter → send_log → events → analytics
```

PII leaves the repository and re-enters only at the rendering step, on the far side
of the LLM. The model sits in the middle of the pipeline and never sees either end
of the customer's identity.

---

## 5. Security Architecture

### 5.1 Defence in depth around the model

```
                    ┌──────────────────────────────┐
   Layer 1          │  No write capability exists  │   physical: file has
   Physical         │  in the agent's DB file      │   one table; RO mode
                    └──────────────┬───────────────┘
                    ┌──────────────▼───────────────┐
   Layer 2          │  mode=ro + PRAGMA query_only │   driver + SQL engine
   Connection       │  + trusted_schema=OFF        │   both reject writes
                    └──────────────┬───────────────┘
                    ┌──────────────▼───────────────┐
   Layer 3          │  Semantic tools only.        │   no SQL, no table name,
   Interface        │  Parameterised, enum-typed.  │   no column list
                    └──────────────┬───────────────┘
                    ┌──────────────▼───────────────┐
   Layer 4          │  account_id bound by the     │   the model has no
   Scope            │  orchestrator, not a param   │   vocabulary for accounts
                    └──────────────┬───────────────┘
                    ┌──────────────▼───────────────┐
   Layer 5          │  Tool output excludes PII    │   CustomerFacts has no
   Data shape       │  by type, not by filtering   │   name/email/phone field
                    └──────────────┬───────────────┘
                    ┌──────────────▼───────────────┐
   Layer 6          │  Policy + content validation │   offers, length, footer,
   Output           │  after every generation      │   placeholders, phrases
                    └──────────────┬───────────────┘
                    ┌──────────────▼───────────────┐
   Layer 7          │  Human approval, hash-bound  │   nothing sends unreviewed
   Human            │                              │
                    └──────────────────────────────┘
```

### 5.2 Threat model

| Threat | Vector | Control | Residual |
|---|---|---|---|
| Model writes to the database | Tool misuse, SQL injection | Layers 1–3; parameterised SQL; write tests | Negligible |
| Model reads another table | Escaped tool layer | Layer 1 — the table is not in the file | None |
| Cross-account read | Model asks for another account | Layer 4 — no parameter exists to ask with | Depends on key-to-account config correctness |
| PII exfiltration through generated content | Model echoes a customer detail | Layer 5 — it never receives one | None for name/email/phone |
| Prompt injection via customer data | Malicious text in a customer field | Free-text fields excluded from tool output; structured returns only | None via this path |
| Prompt injection via user query | Operator sends adversarial `/agent/query` | Scope bound pre-call; model has no write/send/approve capability | Model may produce a wrong *answer*; it cannot take a wrong *action* |
| Policy bypass (excessive discount) | Model generates 50% | Layer 6 rejects; campaign fails loudly | None |
| Content **or audience** swapped after approval | Any post-approval mutation | Hash over content, offer and frozen recipient list, re-verified at send | None |
| Provider abuse / mass send | Compromised key | Role gate on approval, frequency caps, rate limits | Limited by key custody |
| Secret leakage | Logging, tracing, responses | Env-only secrets; redaction tests | Depends on discipline; enforced by test |

### 5.3 Authentication & authorization flow

```
X-API-Key ──► constant-time lookup ──► {principal, account_ids, role}
                                              │
                          ┌───────────────────┴────────────────────┐
                          ▼                                        ▼
              RequestContext (immutable)                    role check
              account_ids fixed for the request        operator | approver
                          │
                          ▼
              ScopedToolset(account_id)  ← the agent is constructed per-request,
                                           already fenced
```

The agent is **not a principal**. It has no key, no identity, no session. It borrows
the caller's fence and cannot see past it.

---

## 6. Deployment Architecture

```
┌──────────────────── Developer machine / single host ────────────────────┐
│                                                                          │
│   docker compose up                                                      │
│   ┌────────────────────────────────────────────────┐                    │
│   │ Container: texting-agent               │                    │
│   │   uvicorn app.main:app  :8000                  │                    │
│   │   volume: ./data → /app/data   (both .db files)│                    │
│   │   volume: ./config → /app/config (read-only)   │                    │
│   │   env: .env                                    │                    │
│   └───────────────┬────────────────────────────────┘                    │
└───────────────────┼──────────────────────────────────────────────────────┘
                    │ HTTPS
      ┌─────────────┼─────────────┬──────────────────┐
      ▼             ▼             ▼                  ▼
  OpenAI API    Serper.dev   Grafana Cloud      (post-MVP: real
                (optional)   OTLP gateway        email/SMS providers)
```

One container. No collector, no Grafana, no Tempo, no Prometheus, no Redis, no
broker — the observability backend is hosted, and everything else the MVP needs is
in-process. The compose file exists to make `docker compose up` plus a seed command
reproduce the system from a clean checkout.

**Environments:** `dev` (mock providers, Serper off, seeded data) and `staging`
(same image, real Grafana Cloud stack, still mock providers). Production is
out of scope until real providers land (PS-01).

---

## 7. Monitoring Architecture

```
   Application
   ├── auto-instrumentation:  FastAPI · sqlite3 · httpx · logging
   └── manual spans:          agent stages · tool calls · validation · sends
                       │
                       ▼
        OpenTelemetry SDK (traces + metrics + logs)
                       │  OTLP/HTTP, Basic auth, batched
                       ▼
        Grafana Cloud OTLP gateway
           ├── Tempo  (traces)   ── one trace per campaign, end to end
           ├── Mimir  (metrics)  ── technical + business series
           └── Loki   (logs)     ── JSON, trace-correlated
                       │
                       ▼
        Dashboards · Alerts (see TRD §12)
```

Three dashboards are enough for v1:

| Dashboard | Answers |
|---|---|
| **Agent Operations** | Is the agent healthy? Stage latency and error rate, LLM latency, token spend per campaign, tool-call mix |
| **Campaign Funnel** | Is the loop working? Analysed → candidates → segments → generated → approved → sent → delivered → converted, with drop-off at each hop |
| **Retention Business** | Is it working *commercially*? Revenue recovered, ROI, reactivation rate, unsubscribe rate, variant performance |

Design rule: **telemetry never affects the request path**. Export failures are
swallowed; a Grafana Cloud outage degrades observability, not service.

---

## 8. Scalability

### Current design limits

| Dimension | v1 limit | Binding constraint |
|---|---|---|
| Customers per account | ~100k | Full-table scan and in-Python scoring per request |
| Accounts | Hundreds | SQLite single-writer on the app DB |
| Concurrent campaigns | Low single digits | Synchronous send loop in the request path |
| Prompt size | **Independent of account size** | Aggregates + capped 50-row samples |
| Cost per campaign | Bounded by token budget | Hard cap, enforced per run |

That last row is the one that matters architecturally: because the model receives
aggregates rather than customers, adding a zero to the customer count changes the
database cost and changes nothing about the LLM cost. The expensive component does
not scale with the data.

### Growth path, in the order it would actually be needed

| Trigger | Change | Blast radius |
|---|---|---|
| Scoring latency noticeable | Materialise `churn_score` in a nightly job; service stays the source of the formula | `scoring_service`, one script |
| Send batches exceed the request timeout | Move sends to a background worker; state machine already models `SENDING` | `communication_service`, one endpoint |
| App DB write contention | PostgreSQL for the app DB | `app_db.py` |
| Multi-tenant scale or genuine DB-level isolation required | PostgreSQL for the agent DB with `agent_ro` role, `GRANT SELECT` on one table, plus RLS on `account_id` | `agent_db.py`, `customer_repo.py` |
| More than one host | Stateless app + shared Postgres; nothing in the app holds state between requests | Deployment only |
| Heuristic scoring outgrown | Replace `scoring_service` internals with a trained model behind the same interface | One module |

Every one of these is a single-module change, because the interfaces were drawn at
those seams from the start. None of them is *built* in v1 — that is the point.

---

## 9. Architecture Decision Records

### ADR-01 — Two SQLite database files rather than one
**Context.** The controlling requirement is that the agent reaches exactly one
table, read-only. SQLite has no roles and no `GRANT`, so "the database enforces it"
is not directly achievable.
**Decision.** Put `customer_agent_records` in its own file, opened read-only with
`query_only`; put all operational tables in a second file the agent package cannot
import a connection to.
**Alternatives.** (a) One file plus discipline — rejected, discipline is not a
control. (b) SQLite authorizer callback — real, but obscure and easy to
mis-configure. (c) Start on PostgreSQL — correct long-term, heavier than the MVP
warrants.
**Consequences.** Genuine enforcement at zero runtime cost; no cross-file joins
(not needed — the agent never joins operational data); a clean, obvious mapping to a
Postgres `GRANT SELECT` role later.

### ADR-02 — Plain OpenAI SDK with structured outputs, not an agent framework
**Context.** The workflow is a fixed pipeline with a deterministic state machine.
**Decision.** Use `openai` with Pydantic structured outputs; five single-shot stage
calls; a small tool-dispatch loop only for `/agent/query`.
**Alternatives.** OpenAI Agents SDK — batteries included, but its autonomy overlaps
the orchestrator, and its tracing is separate from the OTel/Grafana Cloud pipeline.
**Consequences.** Fewer moving parts, uniform tracing, direct control of retries and
token accounting. Cost: tool-loop plumbing for `/agent/query` is hand-written (~60
lines).

### ADR-03 — The model writes templates; code renders values
**Context.** "The agent must never fabricate customer information" needs a
mechanism, not a sentence in a prompt.
**Decision.** The model emits templates with allow-listed placeholders. Deterministic
rendering substitutes real values at send time. The model never receives PII.
**Alternatives.** Per-customer generation with post-hoc PII checking — higher
personalization, but it puts PII in prompts, costs ~100× the tokens, and makes human
approval of hundreds of messages impractical.
**Consequences.** Fabrication and leakage become inexpressible rather than
detectable; approval reviews ~10 variants instead of hundreds of messages.
Trade-off: personalization is per-segment, so richer personalization means finer
segments.

### ADR-04 — Derived fields computed on read, not stored
**Context.** The spec lists `churn_score` and `days_since_*` as columns.
**Decision.** Store facts and timestamps; compute derivations in the scoring service.
**Consequences.** Staleness is impossible and there is no refresh job to own. Costs a
full-account scan per request (~10 ms at 5k rows), with a documented materialisation
path if that ever binds.

### ADR-05 — The model proposes segment definitions; code assigns customers
**Context.** Segmentation needs judgement (which cohorts matter) and precision (who
is in them). Those are different problems.
**Decision.** The model returns structured predicates; `segmentation_service`
evaluates them in priority order.
**Consequences.** Assignment is exact, reproducible and auditable; the prompt stays
small regardless of account size; a customer provably receives one treatment.

### ADR-06 — Policy violations fail the campaign; they are never auto-corrected
**Context.** A generated 50% discount against a 20% cap could be clamped silently.
**Decision.** Fail with the violated rule ids.
**Consequences.** A drifting prompt or a misconfigured policy surfaces immediately
instead of hiding behind a clamp. Costs a regeneration when it happens — which is the
signal you wanted.

### ADR-06a — Trend signals, and value kept out of the risk score
**Context.** The first cut scored churn from point-in-time levels only, and included
a low-AOV term.
**Decision.** Add five prior-period columns so engagement and order counts can be
measured as *change*, carrying 35% of total weight; remove the value term entirely.
**Alternatives.** Rename the reason codes to describe levels — honest, but discards
the more predictive signal. Keep the value term — but value is already its own axis.
**Consequences.** `ENGAGEMENT_DECLINE` means what it says, and "always quiet" becomes
distinguishable from "just went quiet". Value influences the *treatment* through
playbooks, never the *risk*, so budget is not steered toward the customers worth
least. Cost: the upstream feed must supply the prior window; where it cannot,
scoring degrades per-customer to `LOW_ENGAGEMENT` rather than inventing a trend.

### ADR-06b — The approval hash covers the audience, not just the content
**Context.** Hashing only variants and offer meant a re-score between approval and
send could change *who* received a campaign while the hash still validated.
**Decision.** Freeze the resolved recipient list into `campaign_targets` at
`VALIDATED` and include it in the hash. Send-time gates may remove recipients; no
code path may add one.
**Consequences.** The approver signs off on content *and* audience. The remove-only
rule is what stops an ordinary unsubscribe from invalidating the hash and blocking an
otherwise legitimate send.

### ADR-06c — One account per agent run
**Context.** An API key may cover several accounts, but the toolset binds one.
**Decision.** `account_id` is mandatory in the body of `POST /campaigns` and
`POST /agent/query`; 400 if absent, 403 if outside scope. Read endpoints without it
span all in-scope accounts.
**Consequences.** The agent is never constructed with more than one tenant in reach,
so cross-account leakage has no in-memory path to travel, not merely no SQL path.
Cost: multi-brand operators name the account on every write call.

### ADR-07 — Grafana Cloud over OTLP; no self-hosted observability components
**Context.** The original spec called for Alloy/Collector + Tempo + Grafana locally.
**Decision.** Export OTLP/HTTP straight to the Grafana Cloud gateway.
**Consequences.** Identical app-side instrumentation, three fewer services to run
and maintain, no local storage to manage. If a collector is ever needed (sampling,
redaction, fan-out), it inserts between app and gateway with no application change.

### ADR-08 — Mock providers in v1, real providers behind the same interface
**Context.** The closed loop must be demonstrable end to end.
**Decision.** Ship mock email/SMS plus a seeded event simulator.
**Consequences.** The full loop is testable, deterministic and free, with no risk of
sending real mail from a demo system. Real providers become a configuration change
plus one adapter (PS-01).

### ADR-09 — Synchronous sends in v1
**Context.** A queue would be the "proper" answer.
**Decision.** Send inside the request, bounded by batch size.
**Consequences.** No broker, no worker, no queue semantics to debug. The `SENDING`
state already exists, so moving to a worker later is an internal change. Limit:
large batches will need the worker — documented, not pre-built.

### ADR-10 — Security tests are written with each phase, not at the end
**Context.** The spec sequences hardening as the final phase.
**Decision.** Each phase that creates a boundary ships the test that proves it.
**Consequences.** No phase builds on an unverified assumption, and the security suite
is a regression net for every later change rather than a one-time audit.

---

## 10. Quality Attributes Summary

| Attribute | How the architecture delivers it |
|---|---|
| **Security** | Seven-layer defence; the model has no write, send, approve or account capability; PII never enters a prompt |
| **Correctness** | Every number is Python's; the LLM never computes, ranks or assigns |
| **Auditability** | Campaigns record model, prompt version, config version, tokens, LLM cost, validation results, approver, the content-and-audience hash, and the frozen recipient list |
| **Observability** | One trace per campaign spanning every hop; business and technical metrics; trace-correlated JSON logs |
| **Testability** | Deterministic seeds, stubbed LLM, mock providers — the whole loop runs offline |
| **Modifiability** | Business rules are YAML; scoring, providers and the database are single-module swaps |
| **Simplicity** | One agent, one container, two files, no broker, no cache server, no framework |
