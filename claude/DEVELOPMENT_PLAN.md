# Development Plan
## Texting Agent

| Field | Value |
|---|---|
| Document | Development Plan |
| Version | 1.0 |
| Date | 2026-08-29 |
| Related | [PRD.md](PRD.md) · [SRS.md](SRS.md) · [TRD.md](TRD.md) · [ARCHITECTURE.md](ARCHITECTURE.md) |

**How to read this.** Twelve milestones, `M0`–`M11`. Each one is independently
demonstrable, ships its own tests, and leaves the system in a working state. Effort
figures are indicative solo-developer days, not commitments. Nothing here starts
before the milestone it depends on is *done by its own definition of done*.

---

## 1. Sequencing Principles

| # | Principle | Consequence |
|---|---|---|
| 1 | **Boundaries before behaviour** | The read-only, one-table and account-scope boundaries are built and tested in M1, before a single LLM call exists. Everything after is built on a proven fence. |
| 2 | **Deterministic before probabilistic** | Scoring, tiering and playbooks (M2) precede the agent (M5). The LLM is added to a system that already knows who is at risk. |
| 3 | **Every milestone is demonstrable** | If it cannot be shown working, it is not a milestone. |
| 4 | **Tests ship with the code that needs them** | Security tests in particular. Hardening is not a final phase — it is a property of each phase (ADR-10). |
| 5 | **The loop closes before anything is polished** | M9 completes detect → send → measure → optimize. Serper and observability come after, because they improve a working loop rather than create one. |
| 6 | **No speculative work** | If a milestone's task is not traceable to an SRS requirement, it is not in the plan. |

---

## 2. Dependency Graph

```
M0 Foundation
 └─► M1 Data & Security Boundary        ◄── the fence, proven by tests
      └─► M2 Deterministic Intelligence  (scoring, tiering, playbooks)
           └─► M3 Agent Tools & Contracts (ScopedToolset, PII-free facts)
                ├─► M4 API & Auth ───────────────┐
                └─► M5 Agent Core + Orchestrator ┤  (ANALYZE, SEGMENT, /agent/query)
                                                 │
                                    M6 Strategy & Content ◄─┘  (PLAN, GENERATE, render)
                                     └─► M7 Validation, Policy & Approval
                                          └─► M8 Communication & Sending
                                               └─► M9 Analytics, A/B & Optimization
                                                    │        ▲
                                                    │        └── LOOP CLOSES HERE
                                                    ├─► M10 Research + Observability
                                                    └─► M11 Hardening, Demo & Deploy
```

Critical path: **M0 → M1 → M2 → M3 → M5 → M6 → M7 → M8 → M9**.
M4 is parallelisable with M5 once M3 lands. M10 and M11 depend only on M9.

**The UI track runs alongside, not after.**

```
U0 Foundation (tokens, modes)  ──► U1 Hero ──► U2 Sections ──► U3 Polish
                                                                   │
                        M9 (loop closes) ──────────────────────────┴──► U4 Console
```

U0–U3 depend on **no** backend milestone and can start immediately or in parallel
with M0. U4 needs a working API, so it waits for M9. Full detail in
[UIUX.md](UIUX.md) §12.

---

## 3. Priorities

| Priority | Meaning | Milestones |
|---|---|---|
| **P0** | MVP-blocking. Without it there is no product. | M0, M1, M2, M3, M4, M5, M6, M7, M8, M9 |
| **P1** | Required by the specification, not required for the loop to run. | M10 (Serper, observability), M11 (hardening, demo) |
| **P2** | Post-MVP. Named so it is not forgotten; not started in this project. | Real providers, PostgreSQL, ML scoring, scheduling, extra channels |
| **U** | UI track — parallel, own milestones and definition of done in [UIUX.md](UIUX.md). | U0–U3 landing page (now), U4+ operator console (after M9) |

---

## 4. Milestones

### M0 — Foundation `P0` · ~1 day · depends on: nothing

**Goal.** A running, configured, observable-ready skeleton with no domain logic.

| # | Task |
|---|---|
| 0.1 | Project structure per TRD §2; `pyproject.toml` managed by `uv`, `src/` layout, `texting-agent` console script |
| 0.2 | `app/config.py` — `pydantic-settings`, `.env` + YAML loading, fail-fast validation `[VR-11]` |
| 0.3 | `app/observability/logging.py` — structlog JSON, PII-free formatter `[SEC-07]` |
| 0.4 | `app/main.py` — FastAPI app, lifespan, exception handler with correlation ids `[EH-10]` |
| 0.5 | `GET /health` `[FR-67]` |
| 0.6 | `.env.example`, `.gitignore`, `README.md` with quickstart |
| 0.7 | `pytest` wired; one smoke test |

**Deliverable.** `uv run texting-agent` serves `/health`.

> Docker is deliberately not built here. The stated run command is
> `uv run texting-agent`, and a compose file written now — before the databases of
> M1 exist — would only be rewritten. It arrives with M11, where `[AC-18]` needs it.

**Definition of Done**
- [x] `/health` returns 200 with service, config-valid and DB-status fields
- [x] Missing required env aborts startup with a named key `[EH-11]`
- [x] Logs are JSON with the standard field set
- [x] `uv run pytest` passes; `.env` is gitignored and no secrets are in the repo

---

### M1 — Data & Security Boundary `P0` · ~2 days · depends on: M0

**Goal.** The two-database boundary, built and *proven*. This is the milestone the
whole project rests on.

| # | Task |
|---|---|
| 1.1 | `schema_agent.sql` — `customer_agent_records` per TRD §3.2, including the five prior-period columns and the two windowed counters, with CHECKs and index |
| 1.2 | `schema_app.sql` — campaigns, segments, **campaign_targets**, variants, approvals, send_log, events, suppressions, agent_runs |
| 1.3 | `agent_db.py` — read-only engine: `mode=ro&uri=true`, `PRAGMA query_only=ON`, `trusted_schema=OFF` `[SEC-01]` |
| 1.4 | `app_db.py` — read-write engine, separate file |
| 1.5 | `customer_repo.py` — mandatory `account_id`, single `_scoped()` helper, parameterised SQL only `[SEC-04]`, `[SEC-05]` |
| 1.6 | ~~`campaign_repo.py`~~ — **moved to M5.** The schema is created here; the write API is not. Its method signatures follow the orchestrator's state machine, and writing them first would mean guessing at callers that do not exist yet. |
| 1.7 | `scripts/seed_data.py` — 3 accounts x 5,000 plus one deliberately tiny account = 15,012 customers, fixed seed, full spread of risk and value, both windows populated, and the EC-03/04/05/23/24/25 edge cases present, including customers sparse enough to score `UNKNOWN` `[FR-03]`, `[FR-04c]` |
| 1.8 | **Security tests**: write/DDL rejection; `sqlite_master` has exactly one table; repo rejects empty `account_id`; cross-account queries return nothing |
| 1.9 | **Import-boundary test**: `app/agent/**` imports neither `app_db` nor provider modules `[SEC-09]` |

**Deliverable.** Seeded databases; a security suite that fails loudly if the fence
is ever weakened.

**Definition of Done**
- [x] INSERT / UPDATE / DELETE / DROP / ALTER / CREATE / ATTACH on the agent connection all raise `[AC-10]`
- [x] The agent DB file contains exactly one table, and no second file can be attached `[AC-11]`
- [x] No repository method can execute without an `account_id`
- [x] Seeding is reproducible from the fixed seed, and every documented edge case appears in it
- [x] An AST test finds no SQL outside the three sanctioned modules, and no interpolated SQL anywhere

> **Gate.** No later milestone begins until M1's security tests are green. Everything
> downstream assumes this fence holds.

---

### M2 — Deterministic Intelligence `P0` · ~2 days · depends on: M1

**Goal.** The system knows who is at risk, why, and how valuable they are — with no
LLM involved.

| # | Task |
|---|---|
| 2.1 | `config/scoring.yaml` + validating Pydantic model |
| 2.2 | `scoring_service.py` — seven signals, weighted score, renormalisation over available signals `[FR-04]`, `[EC-03]`, `[EC-05]` |
| 2.2a | Trend signals: channel-aware engagement change and 90-day order change `[FR-04a]`, `[FR-04d]` |
| 2.2b | `min_signals_required` → `UNKNOWN` with a null score, counted and campaign-excluded `[FR-04c]` |
| 2.3 | Reason-code emission with evidence values `[FR-06]`, `[FR-07]` |
| 2.4 | On-read computation of `days_since_*` `[FR-08]` |
| 2.5 | `value_service.py` — percentile tiers over purchasers only; non-purchasers → LOW_VALUE; small-account suppression `[FR-09]`, `[FR-09a]`, `[FR-09b]` |
| 2.6 | Data-quality rules DQ-01…DQ-06 applied on read |
| 2.7 | `config/playbooks.yaml` + `playbook_service.py` with startup validation |
| 2.8 | Tests: fixture customers → exact scores; config change changes output; missing-signal cases; percentile boundaries |

**Deliverable.** `score_account("ACC_A")` returns ranked, explained candidates.

**Definition of Done**
- [x] Hand-computed fixtures match to 4 decimal places
- [x] A never-purchased customer scores without the purchase signals, not with a zero for them
- [x] An SMS-primary customer with no email engagement is not scored as disengaged `[FR-04d]`
- [x] `ENGAGEMENT_DECLINE` is emitted only with prior-window data; `LOW_ENGAGEMENT` otherwise
- [x] Value contributes nothing to the churn score
- [x] Zero-spend customers never rank as VIP `[EC-23]`
- [x] A customer with no usable signal returns `UNKNOWN` and is campaign-excluded
- [x] Changing a weight in YAML changes output with no code change `[FR-05]`
- [x] Score is documented as a heuristic ranking in code, API model and docstring `[R8]`

---

### M3 — Agent Tools & Contracts `P0` · ~2 days · depends on: M2

**Goal.** The complete model-callable surface, PII-free by type.

| # | Task |
|---|---|
| 3.1 | `schemas/` — `CustomerRecord` (internal, PII) vs `CustomerFacts` (prompt-safe) `[FR-14]` |
| 3.2 | Enums: `RiskLevel`, `ValueTier`, `ReasonCode` (M2), `Channel`, `OfferType`, `PlaybookId`, `CampaignState`, plus `SegmentPredicate` and its matching rule `[FR-21]` |
| 3.3 | `ScopedToolset` — account bound at construction; no account parameter exists `[FR-13]` |
| 3.4 | `get_churn_summary`, `get_churn_candidates` (limit 1–50), `get_customer_behavior`, `get_segment_statistics` `[FR-12]`, `[FR-15]`. `search_web` ships with the Serper integration in M10; until then it must not appear in the tool registry, and a test asserts the surface is exactly these four |
| 3.5 | JSON tool schemas generated from Pydantic for the OpenAI tool definitions |
| 3.6 | Structured tool errors — no stack traces, SQL or paths `[FR-17]` |
| 3.7 | **PII-boundary test**: no tool output field, at any depth, carries a name, email or phone from the seed data `[SEC-06]` |
| 3.8 | Tool-level isolation test: toolset A never returns account-B rows |

**Deliverable.** A toolset callable from a test without any LLM.

**Definition of Done**
- [x] Exactly the four data tools exist; nothing else is model-callable `[FR-12]` (`search_web` lands in M10)
- [x] No tool signature accepts an account, table, column or SQL fragment
- [x] `get_churn_candidates` hard-caps at 50 regardless of the requested limit
- [x] Serialised tool output contains zero seed PII values `[AC-13]`
- [x] Every tool failure is a structured payload with no stack trace, SQL or path `[FR-17]`

---

### M4 — API & Auth `P0` · ~2 days · depends on: M3 *(parallel with M5)*

**Goal.** A secured HTTP surface over the deterministic intelligence — still no LLM.

> **Split during implementation.** Every route in the contract except `/health`
> needs either a campaign (M5 persistence) or the agent (M5), so M4 delivers the
> machinery and M5 wires the routes to it as they are created. Authentication is a
> **global** dependency with a public allowlist rather than a per-route decorator,
> so an M5 route is protected without opting in — the failure mode of the opt-in
> design is a forgotten decorator, and it fails silently. Tasks 4.3, 4.5, 4.6, 4.8
> and the route half of 4.9 move to M5.

| # | Task |
|---|---|
| 4.1 | `deps.py` — API-key auth, constant-time compare, immutable `RequestContext` `[AU-01]`–`[AU-06]` |
| 4.2 | Scope resolution and the `operator` / `approver` role gate `[AZ-01]`, `[AZ-04]` |
| 4.3 | ~~Campaign read endpoints~~ — **moved to M5**, with the campaigns they read |
| 4.4 | Uniform error envelope with `correlation_id` |
| 4.5 | ~~Out-of-scope resources return 404~~ — **moved to M5**; `require_account` and the envelope exist, the routes that use them do not yet |
| 4.6 | `require_account()`: 400 if absent, 403 if out of scope `[FR-63b]`, `[FR-66]` — applied to `POST /campaigns` and `POST /agent/query` in M5 |
| 4.7 | Rate limiting on `/agent/query` and `/campaigns` `[SEC-13]` |
| 4.8 | ~~OpenAPI examples~~ — **moved to M5**, with the endpoints to give examples of |
| 4.9 | **Auth tests** against probe routes built from the real app factory: both roles, both accounts, absent/wrong key, rate limit, envelope. Per-route isolation tests land with the routes in M5 |

**Deliverable.** Authenticated, scope-enforced API.

**Definition of Done**
- [x] No endpoint except `/health` is reachable without a valid key, including routes added later `[AC-15]`
- [x] An `operator` key receives 403 on approver routes, and an `approver` key 403 on operator routes
- [ ] Cross-account access returns 404 on every route `[AC-12]` — *deferred to M5 with the routes*
- [x] Keys never appear in logs, traces or responses `[AU-05]`
- [x] Every error shares one envelope, and its `correlation_id` matches `X-Request-ID`

---

### M5 — Agent Core & Orchestrator `P0` · ~3 days · depends on: M3

**Goal.** The single agent exists and reasons over grounded data; the state machine
drives it.

| # | Task |
|---|---|
| 5.1 | `openai_client.py` — timeouts, retries with jitter, token accounting, budget cap, per-stage model selection defaulting to `gpt-5-nano` `[EH-01]`, `[FR-27]` |
| 5.2 | `instructions.py` (versioned) + `prompts.py` per-stage builders `[FR-28]` |
| 5.3 | `TextingAgent` — one class, one toolset `[FR-19]` |
| 5.4 | `ANALYZE` stage → `ChurnAnalysis` |
| 5.5 | `SEGMENT` stage → `SegmentationResult` (predicates, never assignments) `[FR-21]` |
| 5.6 | `segmentation_service.py` — priority-ordered predicate evaluation, one segment per customer `[FR-22]`, `[EC-08]` |
| 5.7 | `states.py`, `transitions.py` — conditional-UPDATE guards `[EC-12]`, `[EH-09]` |
| 5.8 | `workflow.py` — pipeline through `SEGMENTED` |
| 5.9 | `/agent/query` tool loop with a hard iteration cap `[FR-65]`, `[EC-18]` |
| 5.9a | `campaign_repo.py` — campaign/segment/target/variant/send/event persistence, written against the state machine that now exists (was M1 task 1.6) |
| 5.10 | `agent_runs` persistence **written by the orchestrator** from the usage record each stage returns, keeping `app/agent/**` free of any app-DB import `[SEC-09]` |
| 5.11 | LLM stub fixtures so the suite is deterministic and offline |
| 5.11a | Live smoke test confirming `gpt-5-nano` honours the strict structured-output schemas against the pinned SDK version |
| 5.12 | **Prompt-injection tests**: scope escape, "run SQL", "list tables", "ignore your instructions" `[SEC-15]` |

**Deliverable.** *Demo checkpoint 1* — "Show me all customers likely to churn"
returns grounded totals, patterns and cohorts.

**Definition of Done**
- [ ] Exactly one agent class in the codebase; no sub-agents `[FR-19]`
- [ ] Every stage output parses into its schema; one retry on parse failure `[VR-04]`
- [ ] Budget overrun fails the run with `BUDGET_EXCEEDED` `[EH-03]`
- [ ] Injection prompts change no scope and reach no forbidden data `[AC-12]`
- [ ] Empty-account and no-candidate paths short-circuit without an LLM call `[EC-01]`, `[EC-02]`

---

### M6 — Strategy & Content `P0` · ~3 days · depends on: M5

**Goal.** Retention plans and A/B message templates, plus deterministic rendering.

| # | Task |
|---|---|
| 6.1 | `PLAN` stage → `RetentionPlanSet`; playbook from the closed enum; no message-count or follow-up fields `[FR-23]`, `[FR-23a]` |
| 6.2 | Channel selection justified by segment engagement rates `[FR-24]` |
| 6.3 | `GENERATE` stage → `MessageVariantSet`, ≥2 variants per channel `[FR-25]`, `[FR-33]` |
| 6.4 | `config/placeholders.yaml` + allowlist enforcement `[FR-29]` |
| 6.5 | `rendering_service.py` — fail-closed substitution, fallbacks, channel escaping `[FR-30]`, `[FR-31]`, `[SEC-11]` |
| 6.6 | Email structure (subject, body, CTA, unsubscribe footer) and SMS structure (body, opt-out) `[FR-32]` |
| 6.7 | `GET /campaigns/{id}/messages` with rendered previews `[FR-34]` |
| 6.8 | Tests: unknown placeholder raises; null without fallback skips; no raw placeholder ever ships |

**Deliverable.** *Demo checkpoint 2* — "Create a retention campaign" then
"Show me the messages".

**Definition of Done**
- [ ] Each segment carries a playbook, an offer, a justified channel decision and ≥2 variants `[AC-2]`
- [ ] Generated content contains no literal name, email, phone or order id `[FR-25]`
- [ ] Every preview renders with all placeholders resolved `[AC-3]`
- [ ] Rendering fails closed in every unresolved case
- [ ] Generated content contains no `customer_id` literal `[FR-25]`
- [ ] **A human reads the generated copy and judges it sendable.** If it is not, that is a model-tier signal — raise `OPENAI_MODEL_GENERATE`, do not patch the prompt around it `[R15]`

---

### M7 — Validation, Policy & Approval `P0` · ~2 days · depends on: M6

**Goal.** Nothing invalid, non-compliant or unapproved can proceed.

| # | Task |
|---|---|
| 7.1 | `config/policy.yaml` + validating model |
| 7.2 | `policy_service.py` — offer caps by tier, allowed types, SMS length, footer, banned phrases, forbidden literals incl. `customer_id`, CTA-key allowlist. No quiet hours `[FR-37]` |
| 7.3 | Business-rule validation layer `[FR-36]` |
| 7.4 | Content-safety validation — no email/phone/URL-outside-allowlist/name-shaped literals `[VR-07]` |
| 7.5 | Violations fail the campaign with rule ids; never auto-corrected `[FR-38]`, ADR-06 |
| 7.6 | Freeze audience into `campaign_targets`, hash content + offer + recipient list, transition to `AWAITING_APPROVAL` `[FR-42]`, `[FR-42a]` |
| 7.7 | `approve` / `reject` / `cancel` endpoints, role-gated, idempotent, state-guarded `[FR-43]`–`[FR-48]` |
| 7.7a | `revise` endpoint: clones a `REJECTED`/`FAILED` campaign to a new one in `RECEIVED`, carrying the reason as agent feedback `[FR-48a]` |
| 7.8 | Approval records approver, timestamp and hash |
| 7.9 | Tests: 50% discount rejected by rule id; double approve → 409; concurrent approve → exactly one wins; operator → 403 |

**Deliverable.** *Demo checkpoint 3* — a campaign held at `AWAITING_APPROVAL`, then
approved; a policy-violating campaign rejected with a named rule.

**Definition of Done**
- [ ] A cap-exceeding offer never reaches `AWAITING_APPROVAL` `[AC-4]`
- [ ] Nothing is silently rewritten to fit policy
- [ ] Approval is idempotent and role-gated `[AC-5]`
- [ ] Every validation result is persisted and returned `[FR-39]`
- [ ] Altering `campaign_targets` after approval breaks the hash and blocks the send `[EC-28]`

---

### M8 — Communication & Sending `P0` · ~2 days · depends on: M7

**Goal.** Approved campaigns reach (mock) customers, with send-time policy enforced.

| # | Task |
|---|---|
| 8.1 | `EmailProvider` / `SMSProvider` protocols + mock implementations with a configurable failure rate `[FR-50]` |
| 8.2 | `communication_service.py` — the only path to a provider `[FR-49]` |
| 8.3 | Hash re-verification over stored content **and** `campaign_targets` before any dispatch `[FR-45]` |
| 8.4 | Send-time gates: suppression, consent, frequency cap. Gates may only remove recipients, never add `[FR-40]`, `[FR-54]`, `[EC-09]`, `[EC-10]` |
| 8.4a | `UNSUBSCRIBED`/`BOUNCED` events write a `suppressions` row in the same transaction `[FR-40a]` |
| 8.5 | Deterministic variant assignment hashed over campaign id, **channel** and customer id, so channels are independent `[FR-53]` |
| 8.6 | `send_log` rows for SENT / FAILED / SKIPPED with reasons `[FR-51]`, `[FR-52]` |
| 8.7 | Retry policy + circuit breaker `[EH-07]`, `[EH-12]` |
| 8.8 | `POST /campaigns/{id}/send`; `UNIQUE(campaign, customer, channel)` makes replay a no-op |
| 8.9 | `POST /campaigns/{id}/simulate-events` (dev-only, 404 unless `ENV=dev`) plus the seeded generator behind it `[FR-55]`, `[FR-63c]` |
| 8.10 | Tests: hash mismatch aborts; suppressed skipped; cap skipped; failure never recorded as sent |

**Deliverable.** *Demo checkpoint 4* — "Send the campaign", followed by a send log
with per-recipient outcomes.

**Definition of Done**
- [ ] No dispatch occurs without an approval and a matching hash `[AC-14]`
- [ ] Every recipient has a terminal status with a reason where applicable `[AC-6]`
- [ ] A provider failure is `FAILED`, never `SENT` `[FR-52]`
- [ ] Re-running a send produces no duplicates

---

### M9 — Analytics, A/B & Optimization `P0` · ~2 days · depends on: M8

**Goal. The loop closes.** Results are measured deterministically and become the
next campaign's input.

| # | Task |
|---|---|
| 9.1 | `analytics_service.py` — all counts and rates, per campaign / segment / variant `[FR-56]`, `[FR-58]` |
| 9.2 | Explicit denominators; zero denominator → `null` `[FR-57]` |
| 9.3 | `campaign_cost` = per-message cost + discount liability + LLM spend; metric named `gross_attributed_roi` with a basis note `[FR-56b]`, `[FR-56c]`, `[FR-62]` |
| 9.4 | Reactivation rate over targets flagged `was_lapsed` at freeze time only `[FR-56d]` |
| 9.4a | Conversion attribution window (default 14 days from that customer's send) `[FR-56a]` |
| 9.5 | Two-proportion z-test + minimum-sample gate → `INSUFFICIENT_DATA` / `SIGNIFICANT` / `NO_DIFFERENCE` `[FR-59]` |
| 9.6 | `OPTIMIZE` stage — receives metrics and the verdict only `[FR-26]` |
| 9.7 | Post-validator: no winner may be named unless the verdict is `SIGNIFICANT` `[FR-60]` |
| 9.8 | `GET /campaigns/{id}/metrics` and `/optimization` |
| 9.9 | Tests: metrics against hand-computed fixtures; z-test against known values; agent cannot name a winner below the gate |

**Deliverable.** *Demo checkpoint 5* — "How did the campaign perform?" then
"Optimize the next campaign". **The full agentic loop runs end to end.**

**Definition of Done**
- [ ] No percentage anywhere is computed by the LLM `[FR-11]`
- [ ] Empty denominators return `null`, never 0 `[AC-7]`
- [ ] Below the sample gate the recommendation explicitly declines to name a winner `[AC-8]`
- [ ] Every observation in the recommendation cites a metric value `[FR-61]`
- [ ] A conversion outside the attribution window contributes no revenue `[FR-56a]`
- [ ] `reactivation_rate` and `conversion_rate` differ on the fixture data `[FR-56d]`
- [ ] Every ROI response carries its "not incremental" basis note `[FR-56c]`

---

### M10 — External Research & Observability `P1` · ~2 days · depends on: M9

**Goal.** Optional market context, and the whole loop visible in Grafana Cloud.

| # | Task |
|---|---|
| 10.1 | `serper.py` — timeout, TTL cache, `SERPER_ENABLED` flag, non-fatal failure `[FR-18]`, `[EH-06]` |
| 10.2 | `search_web` tool; results marked as external context, never a customer fact `[SEC-12]` |
| 10.3 | OTel SDK bootstrap; OTLP/HTTP exporter to Grafana Cloud `[FR-17]` |
| 10.4 | Auto-instrumentation: FastAPI, sqlite3, httpx, logging |
| 10.5 | Manual spans per TRD §12 (agent stages, tool calls, validation, sends, analytics) |
| 10.6 | Technical and business metrics instruments |
| 10.7 | Trace-correlated JSON logs (`trace_id`, `account_id`, `campaign_id`) `[AC-19]` |
| 10.8 | Dashboards: Agent Operations, Campaign Funnel, Retention Business |
| 10.9 | Alerts per TRD §12 |
| 10.10 | Test: exporter failure does not affect request handling `[EC-22]`, `[NFR-08]` |

**Deliverable.** *Demo checkpoint 6* — one Grafana Cloud trace covering an entire
campaign, plus three live dashboards.

**Definition of Done**
- [ ] A campaign produces one connected trace, request to send `[AC-16]`
- [ ] All TRD metrics are visible in Grafana Cloud `[AC-17]`
- [ ] No span attribute or log line carries PII `[SEC-07]`
- [ ] With `SERPER_ENABLED=false`, or with Serper down, campaigns still complete `[AC-9]`

---

### M11 — Hardening, Demo & Deployment `P1` · ~2 days · depends on: M9, M10

**Goal.** Prove the guarantees, package the system, make the demo repeatable.

| # | Task |
|---|---|
| 11.1 | Full adversarial suite: scope escape, PII extraction, write attempts, policy bypass, injected instructions in queries `[SEC-15]` |
| 11.2 | `scripts/verify_security.py` — a single command that asserts every boundary claim |
| 11.3 | Load check: 5,000-customer campaign within the NFR-02 budget |
| 11.4 | Cost check: token spend per campaign within `[NFR-04]` |
| 11.5 | Dependency pinning and vulnerability scan `[SEC-14]` |
| 11.6 | `README.md`: setup, seed, run, demo script for Requests 1–8, security-guarantee summary |
| 11.7 | `docker compose up` verified from a clean checkout `[AC-18]` |
| 11.8 | Requirement traceability check: every SRS `MUST` maps to a passing test |
| 11.9 | Confirm all eight demo requests run through the HTTP API with no manual script step `[AC-9d]` |

**Deliverable.** A packaged, documented, demonstrable system.

**Definition of Done**
- [ ] Every acceptance criterion AC-1…AC-20 passes `[AC-20]`
- [ ] `verify_security.py` is green
- [ ] Clean checkout to working demo in under 10 minutes, following only the README
- [ ] Every SRS `MUST` requirement has an owning test

---

## 5. Effort Summary

| Milestone | Priority | Effort (indicative) | Cumulative |
|---|---|---|---|
| M0 Foundation | P0 | 1 | 1 |
| M1 Data & Security Boundary | P0 | 2 | 3 |
| M2 Deterministic Intelligence | P0 | 2 | 5 |
| M3 Agent Tools & Contracts | P0 | 2 | 7 |
| M4 API & Auth | P0 | 2 | 9 *(parallelisable)* |
| M5 Agent Core & Orchestrator | P0 | 3 | 12 |
| M6 Strategy & Content | P0 | 3 | 15 |
| M7 Validation, Policy & Approval | P0 | 2 | 17 |
| M8 Communication & Sending | P0 | 2 | 19 |
| M9 Analytics, A/B & Optimization | P0 | 2 | **21 — loop closes** |
| M10 Research & Observability | P1 | 2 | 23 |
| M11 Hardening, Demo & Deploy | P1 | 2 | **25 — MVP complete** |

---

## 6. Demo Checkpoints

The specification's eight demo requests map onto the milestones as follows. Each
becomes a permanent regression test, not a one-off script.

| Request | Available from |
|---|---|
| 1. "Show me all customers who are likely to churn." | M5 |
| 2. "Analyze the high-risk customers." | M5 |
| 3. "Create a retention campaign." | M6 |
| 4. "Show me the messages." | M6 |
| 5. "Approve the campaign." | M7 |
| 6. "Send the campaign." | M8 |
| 7. "How did the campaign perform?" | M9 |
| 8. "Optimize the next campaign." | M9 |

---

## 7. Test Strategy

| Layer | Runs | Content |
|---|---|---|
| **Unit** | Every commit | Scoring, tiering, policy, rendering, analytics, transitions. Pure functions with hand-computed fixtures. |
| **Security** | Every commit — non-negotiable | DB boundary, account isolation, PII boundary, import boundary, prompt injection. A failure here blocks the merge. |
| **Contract** | Every commit | Stage outputs parse into their schemas; tool schemas match their Pydantic models. |
| **Integration** | Every commit | API with stubbed LLM and mock providers. |
| **End-to-end** | Every commit | The full loop against stubbed LLM responses — deterministic, offline, free. |
| **Live smoke** | On demand (`RUN_LIVE_LLM_TESTS=1`) | One real OpenAI round trip per stage, to catch schema drift. |

**Rule.** The LLM is stubbed by default. A test suite that needs a paid API call to
tell you whether your code works is a test suite you will stop running.

---

## 8. Risk Register (delivery risks)

| ID | Risk | Trigger | Response |
|---|---|---|---|
| DR-1 | Structured outputs unreliable for a complex schema | Repeated parse failures in M5/M6 | Flatten the schema, split the stage into two calls; the retry path already exists |
| DR-2 | Segment predicates too coarse for good targeting | Segments overlap or are unusably broad in M5 | Extend `SegmentPredicate` with numeric ranges — it is a schema change, not a redesign |
| DR-3 | Generated copy is bland or off-tone | Manual review at M6 | Tune playbook `guidance` in YAML; no code change |
| DR-4 | Token budget exceeded on large accounts | Budget errors in M6 | Reduce the candidate sample; move `GENERATE` to segment-at-a-time calls |
| DR-5 | Grafana Cloud credentials/endpoint friction | M10 setup | The app is unaffected — set `OTEL_EXPORTER_OTLP_ENDPOINT` empty and telemetry no-ops |
| DR-6 | Scope creep into deferred features | Any milestone | The MVP cut line in PRD §5 is the arbiter; deferred items stay in the P2 list |
| DR-7 | Synthetic data unrealistic, hiding edge cases | M2/M9 | Seed generator explicitly produces EC-03, EC-04, EC-05 and EC-15 cases |
| DR-8 | SQLite write contention during send | M8 | Batch writes in one transaction; WAL mode; PostgreSQL is the documented next step |

---

## 9. Global Definition of Done

The project is complete when all of the following hold — not one of them is optional.

**Functional**
- [ ] All eight demo requests work end to end from a clean checkout
- [ ] Acceptance criteria AC-1 … AC-20 pass
- [ ] Every SRS `MUST` requirement has an owning, passing test

**Security** *(any failure blocks release)*
- [ ] The agent cannot write to any database
- [ ] The agent's database file contains exactly one table
- [ ] No cross-account data is reachable, including under adversarial prompts
- [ ] No customer PII appears in any prompt, log or trace
- [ ] No send occurs without an approval whose content-and-audience hash matches
- [ ] No policy-violating offer can reach `VALIDATED`

**Quality**
- [ ] Full suite green, including the security suite
- [ ] Deterministic: seeded data, stubbed LLM, mock providers — repeatable runs
- [ ] Structured JSON logs, PII-free, trace-correlated

**Operational**
- [ ] `docker compose up` + seed = working system in under 10 minutes
- [ ] `.env.example` documents every variable; no secret is committed
- [ ] One Grafana Cloud trace spans a whole campaign; three dashboards live; alerts configured

**Documentation**
- [ ] PRD, SRS, TRD, Architecture and this plan reflect what was actually built
- [ ] README carries the setup and demo script
- [ ] Every architectural deviation from the original specification is recorded with its rationale (TRD §16)

---

## 10. Explicitly Not Built

Listed so that "we forgot" and "we decided not to" stay distinguishable.

| Item | Revisit when |
|---|---|
| Real email/SMS providers | Real recipients exist and deliverability is owned |
| Engagement webhooks | Real providers land |
| PostgreSQL migration | Scale or genuine DB-level role isolation is required |
| Trained ML churn model | Enough labelled churn outcomes exist to train and validate one |
| Campaign scheduling, pause, quiet hours, follow-up cadences | Sends move to a background worker — all four need somewhere to defer to |
| WhatsApp / push channels | Email and SMS are proven in production |
| Holdout control groups | Incremental lift, rather than gross attributed conversion, becomes the success metric |
| Web UI | The UI/UX document is supplied |
| Multi-user RBAC / SSO | More than two roles are actually needed |
