# Technical Requirements Document
## Texting Agent

| Field | Value |
|---|---|
| Document | TRD |
| Version | 1.0 |
| Date | 2026-08-29 |
| Related | [PRD.md](PRD.md) · [SRS.md](SRS.md) · [ARCHITECTURE.md](ARCHITECTURE.md) · [DEVELOPMENT_PLAN.md](DEVELOPMENT_PLAN.md) · [UIUX.md](UIUX.md) |

**Scope.** *How* the requirements in the SRS are implemented: stack, module layout,
schemas, algorithms, contracts, configuration and telemetry. SRS requirement IDs are
cited as `[FR-xx]`, `[SEC-xx]` etc.

---

## 1. Technology Stack

| Layer | Choice | Rationale |
|---|---|---|
| Language | Python 3.13 | Installed; typing and `dataclasses`/`Enum` maturity |
| Packaging | `uv` + hatchling, `src/` layout | One command to run everything: `uv run texting-agent`. `src/` layout is what makes the console script installable |
| API | FastAPI + Uvicorn | Pydantic-native, OpenAPI for free, first-class OTel instrumentation |
| Validation | Pydantic v2 | One schema layer for request bodies, LLM structured output and internal contracts |
| LLM | `openai` SDK, structured outputs | Decision ADR-02. No agent framework — the orchestrator is already the state machine |
| Database | SQLite via stdlib `sqlite3` | Read-only URIs, pragmas and parameter binding are all native. SQLAlchemy Core was specified and dropped in M1: it added a dependency without adding a control, and raw statements are easier to audit `[SEC-04]` |
| Config | YAML + `pydantic-settings` | Business rules as data, secrets as environment |
| HTTP client | `httpx` | Async, timeouts, auto-instrumented |
| Telemetry | OpenTelemetry SDK + OTLP/HTTP exporter to **Grafana Cloud** | ADR-07. No self-hosted collector, Alloy, Tempo or Prometheus |
| Logging | `structlog` JSON, trace-correlated | `[SEC-07]`, `[NFR-07]` |
| Tests | `pytest`, `pytest-asyncio`, `respx` | Security suite is first-class |
| Container | Docker + `docker-compose.yml` (app only) | Grafana Cloud is remote; nothing else to run |

**Deliberately not used:** an agent framework, Celery/queues (sends are synchronous
in v1), Alembic (v1 ships `schema.sql` + a create-if-absent bootstrap), Redis
(in-process TTL cache is enough), scipy (a z-test is `math.erfc`).

---

## 2. Project Structure

```
texting-agent/
├── pyproject.toml                 # uv project; [project.scripts] texting-agent
├── src/texting_agent/
│   ├── cli.py                     # entry point for `uv run texting-agent`
│   ├── main.py                    # FastAPI app, lifespan, OTel bootstrap
│   ├── config.py                  # pydantic-settings; loads .env + YAML configs
│   ├── deps.py                    # auth, scope resolution, request context
│   │
│   ├── api/
│   │   ├── agent.py               # POST /agent/query
│   │   ├── campaigns.py           # campaign CRUD, approval, metrics, optimization
│   │   └── health.py
│   │
│   ├── agent/
│   │   ├── texting_agent.py       # THE single agent: 5 stages + tool loop
│   │   ├── tools.py               # ScopedToolset: the only model-callable surface
│   │   ├── instructions.py        # system instructions (versioned)
│   │   └── prompts.py             # per-stage prompt builders
│   │
│   ├── orchestrator/
│   │   ├── states.py              # CampaignState enum
│   │   ├── transitions.py         # legal transition table + guards
│   │   └── workflow.py            # deterministic pipeline driver
│   │
│   ├── database/
│   │   ├── agent_db.py            # READ-ONLY engine for customer_agent.db
│   │   ├── app_db.py              # read-write engine for app.db
│   │   ├── schema_agent.sql
│   │   ├── schema_app.sql
│   │   └── repositories/
│   │       ├── customer_repo.py   # ONLY module with customer SQL; always scoped
│   │       └── campaign_repo.py
│   │
│   ├── services/
│   │   ├── scoring_service.py     # churn score + reason codes
│   │   ├── value_service.py       # percentile value tiers
│   │   ├── segmentation_service.py# evaluates model-proposed predicates
│   │   ├── playbook_service.py    # loads/validates playbooks.yaml
│   │   ├── policy_service.py      # policy.yaml enforcement
│   │   ├── rendering_service.py   # placeholder substitution, fails closed
│   │   ├── communication_service.py
│   │   ├── analytics_service.py   # rates, ROI, z-test
│   │   └── event_simulator.py     # dev-only engagement events
│   │
│   ├── integrations/
│   │   ├── openai_client.py       # retries, timeouts, token accounting
│   │   ├── serper.py              # optional, cached, non-fatal
│   │   ├── email_provider.py      # interface + MockEmailProvider
│   │   └── sms_provider.py        # interface + MockSMSProvider
│   │
│   ├── schemas/
│   │   ├── customer.py  churn.py  campaign.py  analytics.py  api.py
│   │
│   └── observability/
│       ├── tracing.py  metrics.py  logging.py
│
├── config/
│   ├── scoring.yaml  playbooks.yaml  policy.yaml  placeholders.yaml
│
├── scripts/
│   ├── seed_data.py  verify_security.py   # simulation is a dev-only endpoint
│
├── data/
│   ├── customer_agent.db          # ONE table, agent-readable
│   └── app.db                     # everything else, agent-unreachable
│
├── tests/
│   ├── security/                  # boundary, isolation, PII, injection
│   └── ...
│
├── web/                           # Next.js landing page + console (UIUX.md)
│
├── claude/                        # project documentation
│   └── PRD.md  SRS.md  TRD.md  ARCHITECTURE.md  DEVELOPMENT_PLAN.md  UIUX.md
│
├── .env.example  README.md
└── CLAUDE.md                      # agent guidelines (root, auto-loaded)
```

---

## 3. Database Design

### 3.1 The two-file boundary — how `[SEC-01]`/`[SEC-02]` are actually enforced

SQLite has no roles and no `GRANT`, so the specification's "the database enforces
read-only" cannot be met with permissions. It is met structurally instead:

```
data/customer_agent.db          data/app.db
┌──────────────────────────┐    ┌─────────────────────────────┐
│ customer_agent_records   │    │ campaigns                   │
│ (the only table present) │    │ campaign_segments           │
└──────────────────────────┘    │ campaign_targets            │
        ▲                       │ message_variants            │
        │ sqlite:///file:...    │ campaign_approvals          │
        │   ?mode=ro&uri=true   │ send_log                    │
        │ PRAGMA query_only=ON  │ engagement_events           │
        │ PRAGMA trusted_schema │ suppressions                │
        │                       │ agent_runs                  │
   ScopedToolset                └─────────────────────────────┘
   (the agent)                            ▲
                                          │ read-write
                                   services only — no import path
                                   from app/agent/** to app_db
```

Three independent layers must all fail before the agent can write or read wider:

1. **Physical** — the file it can open contains one table, and no second file can
   be attached to the connection.
2. **Connection** — `mode=ro` makes the OS/driver reject writes; `query_only=ON`
   rejects them again at the SQL layer.
3. **Interface** — the model gets semantic tools, never SQL `[FR-13]`, `[SEC-03]`.

```python
# src/texting_agent/database/agent_db.py — the ONLY way to reach customer data
conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
conn.execute("PRAGMA query_only = ON")
conn.execute("PRAGMA trusted_schema = OFF")
conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
```

`mode=ro` blocks writes but not *reads of a second file*: without the attached-database
limit, one `ATTACH` would put app state in reach of the same statement. Found by the
M1 boundary test, which asserts the rejection rather than the intent.

An import-boundary test asserts that nothing under `app/agent/` imports `app_db`
or any provider module `[SEC-09]`.

**PostgreSQL migration path** — the same guarantee becomes native:

```sql
CREATE ROLE agent_ro LOGIN;
REVOKE ALL ON SCHEMA public FROM agent_ro;
GRANT USAGE ON SCHEMA agent FROM ...;
GRANT SELECT ON agent.customer_agent_records TO agent_ro;
-- plus RLS: USING (account_id = current_setting('app.account_id'))
```

Only `agent_db.py` and `customer_repo.py` change `[NFR-06]`.

### 3.2 `customer_agent_records`

```sql
CREATE TABLE customer_agent_records (
    account_id             TEXT    NOT NULL,
    customer_id            TEXT    NOT NULL,
    customer_name          TEXT,
    email                  TEXT,
    phone                  TEXT,
    customer_status        TEXT    NOT NULL DEFAULT 'ACTIVE',
    registration_date      TEXT    NOT NULL,
    last_activity_at       TEXT,
    last_login_at          TEXT,
    last_purchase_at       TEXT,
    total_orders           INTEGER NOT NULL DEFAULT 0,
    total_spend            REAL    NOT NULL DEFAULT 0,
    average_order_value    REAL,
    purchase_frequency_days REAL,
    email_open_rate        REAL,
    email_click_rate       REAL,
    sms_response_rate      REAL,
    -- prior-period columns: enable genuine TREND signals  [FR-04a]
    email_open_rate_prev_90d  REAL,
    sms_response_rate_prev_90d REAL,
    orders_last_90d        INTEGER NOT NULL DEFAULT 0,
    orders_prev_90d        INTEGER NOT NULL DEFAULT 0,
    -- windowed counters: no tenure bias  [FR-04b]
    cart_abandonment_count_90d INTEGER NOT NULL DEFAULT 0,
    support_issue_count_90d    INTEGER NOT NULL DEFAULT 0,
    preferred_channel      TEXT,
    email_consent          INTEGER NOT NULL DEFAULT 0,
    sms_consent            INTEGER NOT NULL DEFAULT 0,
    last_purchase_category TEXT,
    data_as_of             TEXT    NOT NULL,
    PRIMARY KEY (account_id, customer_id),
    CHECK (total_orders >= 0 AND total_spend >= 0),
    CHECK (email_consent IN (0,1) AND sms_consent IN (0,1))
);
CREATE INDEX idx_car_account_status ON customer_agent_records(account_id, customer_status);
```

**Window definitions.** Two fixed 90-day windows, both anchored on `data_as_of`:

| Window | Span | Columns |
|---|---|---|
| **current** | `data_as_of − 90d` … `data_as_of` | `email_open_rate`, `email_click_rate`, `sms_response_rate`, `orders_last_90d`, `cart_abandonment_count_90d`, `support_issue_count_90d` |
| **prior** | `data_as_of − 180d` … `data_as_of − 90d` | `*_prev_90d`, `orders_prev_90d` |

Engagement rates are **trailing-90-day**, not lifetime. `total_orders` and
`total_spend` stay lifetime — value tiering needs the full history.

Without the prior window there is no way to tell a customer who was always quiet
from one who just went quiet, and the second is the far more actionable signal.
That distinction is the whole point of these five columns.

**No derived columns** `[FR-08]`. `days_since_*`, `churn_score`, `churn_risk_level`,
`reason_codes` and `value_tier` are computed on read by the scoring and value
services. Stored derivations go stale silently; computed ones cannot.

> `ponytail:` full-account scan and score in Python — ~10 ms at 5k rows, ~200 ms at
> 100k. If that stops being acceptable, materialise `churn_score` in a nightly job
> and keep the service as the single source of the formula.

### 3.3 App DB (abridged)

```sql
CREATE TABLE campaigns (
    campaign_id TEXT PRIMARY KEY,
    account_id  TEXT NOT NULL,
    state       TEXT NOT NULL,
    goal        TEXT,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    content_hash TEXT,              -- content + audience, set at VALIDATED  [FR-42]
    model_id     TEXT,              -- reproducibility   [NFR-11]
    prompt_version TEXT,
    config_version TEXT,
    tokens_in INTEGER, tokens_out INTEGER, llm_cost_usd REAL,
    excluded_stale_count INTEGER NOT NULL DEFAULT 0,   -- [FR-10a]
    revised_from TEXT,              -- prior campaign this one revises  [FR-48a]
    failure_code TEXT, failure_detail TEXT
);

-- The frozen audience. Written at VALIDATED, before the hash is computed.
-- Send-time gates may SKIP rows here; nothing may ever ADD one.  [FR-42a]
CREATE TABLE campaign_targets (
    campaign_id TEXT NOT NULL, segment_id TEXT NOT NULL,
    account_id  TEXT NOT NULL, customer_id TEXT NOT NULL,
    was_lapsed  INTEGER NOT NULL,   -- snapshot for reactivation_rate  [FR-56a]
    PRIMARY KEY (campaign_id, customer_id)
);

CREATE TABLE campaign_segments (
    segment_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL,
    name TEXT NOT NULL, priority INTEGER NOT NULL,
    predicate_json TEXT NOT NULL,   -- structured, not SQL  [FR-21]
    playbook_id TEXT NOT NULL, offer_json TEXT NOT NULL,
    channels TEXT NOT NULL, customer_count INTEGER NOT NULL,
    rationale TEXT
);

CREATE TABLE message_variants (
    variant_id TEXT PRIMARY KEY, segment_id TEXT NOT NULL,
    channel TEXT NOT NULL, label TEXT NOT NULL,      -- 'A' | 'B'
    subject_template TEXT, body_template TEXT NOT NULL,
    cta_text TEXT, cta_url TEXT
);

CREATE TABLE campaign_approvals (
    campaign_id TEXT NOT NULL, decision TEXT NOT NULL,
    approver_id TEXT NOT NULL, content_hash TEXT NOT NULL,
    reason TEXT, decided_at TEXT NOT NULL,
    PRIMARY KEY (campaign_id, decided_at)
);

CREATE TABLE send_log (
    send_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL,
    segment_id TEXT NOT NULL, variant_id TEXT,
    account_id TEXT NOT NULL, customer_id TEXT NOT NULL,
    channel TEXT NOT NULL, status TEXT NOT NULL,     -- SENT|FAILED|SKIPPED
    skip_reason TEXT, provider_message_id TEXT,
    error TEXT, attempted_at TEXT NOT NULL,
    UNIQUE (campaign_id, customer_id, channel)       -- idempotent sends
);

CREATE TABLE engagement_events (
    event_id TEXT PRIMARY KEY, send_id TEXT NOT NULL,
    event_type TEXT NOT NULL,   -- DELIVERED|OPENED|CLICKED|CONVERTED|UNSUBSCRIBED|BOUNCED
    revenue REAL, occurred_at TEXT NOT NULL
);

CREATE TABLE suppressions (
    account_id TEXT NOT NULL, customer_id TEXT NOT NULL,
    channel TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (account_id, customer_id, channel)
);

CREATE TABLE agent_runs (
    run_id TEXT PRIMARY KEY, campaign_id TEXT, account_id TEXT NOT NULL,
    stage TEXT NOT NULL, model_id TEXT NOT NULL,
    tokens_in INTEGER, tokens_out INTEGER, latency_ms INTEGER,
    status TEXT NOT NULL, error TEXT, trace_id TEXT, created_at TEXT NOT NULL
);
```

### 3.4 Scoped query helper — the single SQL chokepoint `[SEC-04]`, `[SEC-05]`

```python
# app/database/repositories/customer_repo.py
class CustomerRepository:
    """The only module that writes customer SQL. account_id is mandatory."""

    def _scoped(self, account_id: str, where: str = "", params: dict | None = None):
        if not account_id:
            raise ValueError("account_id is required")           # no default path
        sql = f"SELECT {COLUMNS} FROM customer_agent_records WHERE account_id = :aid {where}"
        return self._conn.execute(text(sql), {"aid": account_id, **(params or {})})
```

`where` fragments come only from module-level constants — never from a caller,
never from the model. A test greps the package for f-string SQL outside this file.

---

## 4. Churn Scoring `[FR-04]`–`[FR-07]`

Deterministic, configurable, explainable. Each signal normalises to 0–1 where
higher means worse.

Seven signals. Three measure a **level**, three measure a **trend**, one is mixed —
and the trend signals carry 35% of the weight, because "went quiet" predicts churn
far better than "is quiet".

```
# --- level signals ---
recency          = clamp(days_since_activity / inactivity_horizon_days, 0, 1)
purchase_gap     = clamp(days_since_purchase / max(expected_interval_days, floor), 0, 1)
                   expected_interval_days = purchase_frequency_days
                                            or tenure_days / total_orders   (orders > 0)
login_lapse      = clamp(days_since_login / inactivity_horizon_days, 0, 1)

# --- trend signals ---
engagement       = clamp(1 - engagement_now / engagement_prev, 0, 1)     → ENGAGEMENT_DECLINE
   engagement_now  = max(email_open_rate      / baseline_email_open_rate,
                         sms_response_rate    / baseline_sms_response_rate)
   engagement_prev = max(email_open_rate_prev_90d  / baseline_email_open_rate,
                         sms_response_rate_prev_90d / baseline_sms_response_rate)
   # no prior data → fall back to level, and emit LOW_ENGAGEMENT instead:
   engagement       = clamp(1 - engagement_now, 0, 1)                     → LOW_ENGAGEMENT

purchase_decline = clamp(1 - orders_last_90d / orders_prev_90d, 0, 1)     (prev > 0)

# --- windowed counters ---
cart_abandon     = clamp(cart_abandonment_count_90d / abandon_cap, 0, 1)
support          = clamp(support_issue_count_90d    / support_cap, 0, 1)

churn_score = Σ(wᵢ · sᵢ) / Σ(wᵢ)        # only over signals with data present
```

**Engagement is channel-aware** — `max()` over the customer's channels, each
normalised against its own baseline. An SMS-primary customer who never opens email
is no longer scored as maximally disengaged, which was a guaranteed false positive
and contradicted the channel-selection logic in `[FR-24]`.

**Value does not enter the risk score.** There is no low-AOV signal: value is a
separate axis (`value_tier`), and feeding it into risk too would inflate scores for
low-value customers — spending retention budget exactly where its return is worst.

Renormalising over *available* signals is what makes `[EC-03]` and `[EC-05]` behave:
a never-purchased customer simply drops `purchase_gap` and `purchase_decline` rather
than scoring 0 for them.

```yaml
# config/scoring.yaml
version: 1
weights:                          # must sum to 1.0; validated at startup
  recency: 0.20                   # → DORMANCY
  purchase_gap: 0.20              # → PURCHASE_GAP
  engagement: 0.20                # → ENGAGEMENT_DECLINE | LOW_ENGAGEMENT
  purchase_decline: 0.15          # → PURCHASE_DECLINE
  login_lapse: 0.10               # → LOGIN_LAPSE
  cart_abandon: 0.10              # → CART_ABANDONMENT
  support: 0.05                   # → SUPPORT_FRICTION
normalisation:
  inactivity_horizon_days: 90
  expected_interval_floor_days: 7
  baseline_email_open_rate: 0.25
  baseline_sms_response_rate: 0.10
  abandon_cap: 3                  # per 90 days
  support_cap: 2                  # per 90 days
thresholds: { critical: 0.80, high: 0.60, medium: 0.35 }
reason_threshold: 0.60            # a signal at/above this becomes a reason code
min_signals_required: 2
value:
  vip_pct: 0.05                   # top 5% of purchasers by spend
  high_pct: 0.20                  # next 20%
  standard_pct: 0.50              # next 50%; the remainder is LOW_VALUE
  min_purchasers_for_tiering: 20
data_quality:
  freshness_window_days: 7        # data_as_of older than this is stale [FR-10]
```

**`min_signals_required`** `[FR-04c]` — a customer with fewer than this many signals
carrying usable data gets `churn_risk_level = UNKNOWN` and `churn_score = null`.
`UNKNOWN` customers are counted and reported, and are **excluded from campaign
targeting**: a score derived from one signal is a guess wearing a decimal point.

**A zero counter scores but does not count** `[RV-M2a]`. `cart_abandonment_count_90d`
and `support_issue_count_90d` are `NOT NULL DEFAULT 0`, so taken literally they are
always "present" and `UNKNOWN` becomes unreachable — the gate would never fire. But a
zero counter cannot distinguish *no events occurred* from *this account does not track
this*. So the counters always contribute to the **score** (no abandonment genuinely is
lower risk) and never to the **confidence gate**. A customer known only by two zeroes
is `UNKNOWN`.

**Reason codes** `[FR-06]`, `[FR-07]` — one code per signal, so every code traces to
exactly one formula and one set of fields. Every signal at or above
`reason_threshold` emits its code, ordered by weighted contribution, with evidence:

```json
[{"code": "ENGAGEMENT_DECLINE", "contribution": 0.19,
  "evidence": {"email_open_rate": 0.06, "email_open_rate_prev_90d": 0.22,
               "change_pct": -73, "channel": "EMAIL"}},
 {"code": "PURCHASE_GAP", "contribution": 0.18,
  "evidence": {"days_since_purchase": 63, "expected_interval_days": 21}}]
```

These evidence objects are the *only* factual basis the agent is given, which is
what makes "do not fabricate" enforceable rather than aspirational `[R1]`.

**Honest limitation, stated in the API response and the agent instructions:**
`churn_score` is a weighted heuristic ranking. It is not calibrated, and 0.87 does
not mean an 87% chance of churning `[A3]`, `[R8]`.

### Value tiering `[FR-09]`

```
total_orders == 0  or  total_spend == 0   →  LOW_VALUE        (no percentile needed)

purchasers (total_orders > 0), ranked by total_spend within the account:
    top 5%    → VIP
    next 20%  → HIGH_VALUE
    next 50%  → STANDARD
    remainder → LOW_VALUE

purchaser count < min_purchasers_for_tiering (default 20)
    → every purchaser is STANDARD, and the campaign response says why
```

Non-purchasers are routed to `LOW_VALUE` directly rather than ranked. Percentiles
over a population that is mostly zeros produce ties the tie-breaker cannot resolve,
which would label arbitrary never-purchasers as VIP `[EC-23]`. Percentiles rather
than currency thresholds, so the same code serves a boutique and an enterprise
account.

---

## 5. The Agent

### 5.1 Shape

One class, one instruction set, one toolset. Five structured stages driven by the
orchestrator, plus one tool-calling loop for `/agent/query` `[FR-19]`, `[FR-20]`.

```python
class TextingAgent:
    def __init__(self, client, toolset: ScopedToolset, budget: TokenBudget): ...

    async def analyze(self, ctx: AnalysisContext)   -> ChurnAnalysis
    async def segment(self, ctx: AnalysisContext)   -> SegmentationResult
    async def plan(self, ctx: PlanningContext)      -> RetentionPlanSet
    async def generate(self, ctx: GenerationContext)-> MessageVariantSet
    async def optimize(self, ctx: ResultsContext)   -> OptimizationRecommendation
    async def query(self, question: str)            -> AgentQueryResult   # tool loop
```

Stages 1–5 are single `responses.parse(...)` calls with a Pydantic `text_format` —
no tool loop, no autonomy, deterministic control flow. Only `query()` loops, and it
is capped at `AGENT_MAX_TOOL_ITERATIONS` (default 6) `[EC-18]`.

### 5.2 Toolset — the entire model-callable surface `[FR-12]`

```python
class ScopedToolset:
    def __init__(self, account_id: str, repo: CustomerRepository):
        self._account_id = account_id     # bound here; never a tool parameter
```

| Tool | Model-visible parameters | Returns |
|---|---|---|
| `get_churn_summary` | *(none)* | totals, counts by risk level and value tier, reason-code frequency distribution, median days-since-purchase |
| `get_churn_candidates` | `risk_level?`, `value_tier?`, `reason_code?`, `limit` (1–50) | matching count + capped PII-free sample with score and reason evidence |
| `get_customer_behavior` | `customer_id` | one PII-free behaviour record with reasons |
| `get_segment_statistics` | `predicate` (structured) | size, share of targetable, mean score, channel engagement means, tier mix, top reason codes |
| `search_web` | `query` | Serper snippets — optional, off by default `[FR-18]`, added in M10 |

No `account_id`, no table name, no column list, no SQL, no free text beyond the
Serper query `[FR-13]`, `[SEC-03]`.

### 5.3 PII boundary `[FR-14]`, `[SEC-06]`

Two models over the same row:

```python
class CustomerRecord(BaseModel):      # internal — rendering & sending only
    customer_id: str; customer_name: str | None
    email: str | None; phone: str | None
    ...

class CustomerFacts(BaseModel):       # the ONLY shape that can reach a prompt
    customer_id: str
    risk_level: RiskLevel             # incl. UNKNOWN  [RV-A1]
    churn_score: float | None         # null when UNKNOWN
    value_tier: ValueTier
    days_since_activity: int | None; days_since_purchase: int | None
    total_orders: int; total_spend: float
    email_open_rate: float | None; sms_response_rate: float | None
    email_open_rate_prev_90d: float | None            # trend basis  [RV-B3]
    sms_response_rate_prev_90d: float | None
    orders_last_90d: int; orders_prev_90d: int
    reasons: list[ReasonEvidence]     # code + contribution + evidence values
    stale: bool                       # data_as_of older than the freshness window
    # name / email / phone deliberately absent
```

`UNKNOWN`-risk and stale customers are returned by `get_churn_summary` and
`get_customer_behavior` — they are reported, not hidden — but never appear in
`get_churn_candidates` or `get_segment_statistics`, which are the two tools whose
output becomes a campaign audience `[FR-04c]`, `[FR-10a]`.

`CustomerFacts` is what `ScopedToolset` returns. A test asserts that no serialised
LLM request payload contains a name, email or phone value present in the seed data
`[AC-13]`. Free-text customer fields are excluded entirely, which closes the
prompt-injection-via-data path `[EC-16]`, `[R4]`.

### 5.4 Stage contracts

**ANALYZE** — in: churn summary + reason distribution + optional market context.
Out:

```python
class ChurnAnalysis(BaseModel):
    headline: str
    dominant_patterns: list[Pattern]        # code + share + interpretation
    cohorts_of_concern: list[str]
    caveats: list[str]                      # must state score is heuristic
```

**SEGMENT** — the model proposes *definitions*, not assignments `[FR-21]`:

```python
class SegmentPredicate(BaseModel):
    risk_levels: list[RiskLevel]
    value_tiers: list[ValueTier]
    required_reason_codes: list[ReasonCode] = []
    excluded_reason_codes: list[ReasonCode] = []

class ProposedSegment(BaseModel):
    name: str; priority: int
    predicate: SegmentPredicate
    hypothesis: str                          # why these customers are leaving

class SegmentationResult(BaseModel):
    segments: list[ProposedSegment] = Field(min_length=1, max_length=6)
```

`segmentation_service` evaluates predicates in priority order; a customer matches at
most one segment `[FR-22]`, `[EC-08]`. Empty segments are dropped with a reason
`[EC-06]`.

**PLAN** — one plan per surviving segment:

```python
class Offer(BaseModel):
    type: OfferType                      # PERCENTAGE_DISCOUNT | FIXED_DISCOUNT
                                         # | LOYALTY_POINTS | FREE_SHIPPING
                                         # | EARLY_ACCESS | NONE
    value: float = 0

class RetentionPlan(BaseModel):
    segment_name: str
    playbook_id: PlaybookId              # closed enum from playbooks.yaml [FR-23]
    offer: Offer
    channels: list[Channel]              # EMAIL | SMS
    channel_rationale: str               # must cite engagement rates [FR-24]
    variants_per_channel: int = Field(ge=2, le=3)
```

`message_count` and `followup_days` are deliberately absent. v1 sends **one message
per selected channel** and has no scheduler, so a follow-up cadence could never
fire — a field the system cannot honour is worse than no field, because it implies a
capability to whoever reads the plan `[RV-C5]`.

**GENERATE** — templates only `[FR-25]`, `[FR-29]`:

```python
class MessageVariant(BaseModel):
    channel: Channel; label: str          # 'A' | 'B'
    subject_template: str | None          # email only
    body_template: str
    cta_text: str | None; cta_url_key: str | None   # key into config, not a raw URL
```

**OPTIMIZE** — receives computed metrics and the statistical verdict only
`[FR-26]`, `[FR-60]`:

```python
class OptimizationRecommendation(BaseModel):
    verdict_ack: StatVerdict              # echoes the verdict it was given
    winning_variant: str | None           # MUST be None if INSUFFICIENT_DATA
    observations: list[str]               # each cites a metric value
    next_experiment: Experiment
    segment_adjustments: list[str]
```

A post-validator enforces `winning_variant is None` whenever the verdict is
`INSUFFICIENT_DATA` or `NO_DIFFERENCE` — the model cannot override the statistics.

### 5.5 Instructions (summary of `instructions.py`)

Versioned string constant recorded on each campaign `[NFR-11]`. Core clauses:

- Every factual claim must trace to a supplied reason code or aggregate. If the data
  does not support a statement, say so rather than filling the gap.
- `churn_score` is a heuristic ranking, not a probability.
- Never emit a customer name, email address, phone number or order id. Use
  placeholders from the allowlist.
- Select a playbook from the supplied list. Do not invent offers or business policy.
- Never declare an A/B winner unless the supplied verdict is `SIGNIFICANT`.

These are guidance, not the control. Every one of them also has a deterministic
validator behind it — the prompt reduces failures, the validator prevents them
`[Rule 15]`.

### 5.6 LLM client

**Model selection — `gpt-5-nano` everywhere, configured per stage.**

```python
STAGE_MODELS = {                    # each independently overridable
    "analyze":  env("OPENAI_MODEL_ANALYZE",  "gpt-5-nano"),
    "segment":  env("OPENAI_MODEL_SEGMENT",  "gpt-5-nano"),
    "plan":     env("OPENAI_MODEL_PLAN",     "gpt-5-nano"),
    "generate": env("OPENAI_MODEL_GENERATE", "gpt-5-nano"),
    "optimize": env("OPENAI_MODEL_OPTIMIZE", "gpt-5-nano"),
    "query":    env("OPENAI_MODEL_QUERY",    "gpt-5-nano"),
}
```

`gpt-5-nano` is the cheapest model on OpenAI's current price list — $0.05 / 1M input,
$0.40 / 1M output. Against the 60,000-token cap in `[NFR-04]` that is roughly
**$0.008 per campaign**.

Six variables rather than one is not indulgence: nano is the weakest tier, and if
`GENERATE` produces flat copy or `SEGMENT` fights the nested schema, the fix is one
environment variable on one stage rather than a code change or a blanket upgrade of
all six. The exact structured-output call shape is confirmed against the pinned SDK
version by the live smoke test (§15) before M5 depends on it.

Wrapper responsibilities: 60 s timeout, 3 retries with exponential backoff and jitter
on 429/5xx/timeout `[EH-01]`, one schema-error re-ask `[EH-02]`, token accounting
against `TokenBudget` `[FR-27]`, `[EH-03]`, and a span per call carrying model,
stage, tokens and latency.

---

## 6. Orchestrator

### 6.1 States `[FR-63]`

```
RECEIVED → ANALYZING → SEGMENTED → PLANNED → CONTENT_READY
        → VALIDATED → AWAITING_APPROVAL → APPROVED → SENDING → SENT   (terminal)
Terminal off-ramps from any non-terminal state: FAILED, CANCELLED
From AWAITING_APPROVAL only: REJECTED
```

Thirteen states. Trimmed from the original 17: `CHURN_ANALYSIS_COMPLETE` and
`SEGMENTATION_COMPLETE` collapse into `SEGMENTED`; `SCHEDULED` and `PAUSED` are
dropped because v1 sends synchronously and has no running state to pause;
**`MEASURED` and `OPTIMIZED` are dropped** because nothing could reach them —
metrics and optimization are `GET`s, and a read must not mutate state `[RV-A5]`.
`SENT` is terminal, and analytics is a pure projection over `send_log` and
`engagement_events` that can be recomputed at any time.

**Revision** `[FR-48a]`. `REJECTED` and `FAILED` stay terminal for that campaign;
`POST /campaigns/{id}/revise` creates a **new** campaign in `RECEIVED` with
`revised_from` set, and feeds the rejection reason into the ANALYZE and PLAN prompts
as operator feedback. No state is reopened, so the audit trail of what was rejected
stays intact.

### 6.2 Transitions

A single `ALLOWED: dict[State, set[State]]` table. `transition(campaign, to)`
performs a conditional UPDATE (`WHERE state = :from`) so two concurrent approvals
cannot both win `[EC-12]`; a zero-row result raises `InvalidTransition` → 409
`[EH-09]`.

### 6.3 Pipeline

```
POST /campaigns  { account_id, goal, ... }
  ├─ resolve scope from API key                      [AZ-01]
  ├─ require body account_id ∈ scope   400 / 403     [FR-66]
  ├─ score + tier that ONE account's customers       [FR-04][FR-09]
  ├─ drop UNKNOWN-risk and stale customers, count them [FR-04c][FR-10a]
  ├─ short-circuit if no candidates                  [EC-01][EC-02]
  ├─ ANALYZE   → ChurnAnalysis
  ├─ SEGMENT   → predicates → deterministic assignment[FR-22]
  ├─ (optional) search_web for market context        [FR-18]
  ├─ PLAN      → RetentionPlan per segment
  ├─ GENERATE  → variants per segment/channel
  ├─ validate: pydantic → business rules → policy    [VR-04..VR-08]
  ├─ FREEZE audience → campaign_targets              [FR-42a]
  ├─ hash(content + offer + audience), persist       [FR-42]
  ├─ → AWAITING_APPROVAL
  └─ return campaign
```

`POST /campaigns` and `POST /agent/query` return **503 `LLM_NOT_CONFIGURED`**
naming `OPENAI_API_KEY` when it is unset, rather than surfacing an SDK error as a
500. Startup logs a warning instead of refusing to boot, so every deterministic
route — health, scoring, campaign listing — still works without a model key
`[EH-11]`.

Only the five bracketed stages call the LLM. Everything else is ordinary code. The
orchestrator — not the agent — writes every `agent_runs` row from the usage record
each stage returns, which is what keeps `app/agent/**` free of any `app_db` import
`[SEC-09]`, `[RV-C4]`.

---

## 7. Policy Engine `[FR-37]`, `[FR-38]`

Rules are data; the engine is a loop.

```yaml
# config/policy.yaml
version: 1
offers:
  allowed_types: [PERCENTAGE_DISCOUNT, FIXED_DISCOUNT, LOYALTY_POINTS,
                  FREE_SHIPPING, EARLY_ACCESS, NONE]
  max_discount_pct_by_tier: { VIP: 25, HIGH_VALUE: 20, STANDARD: 15, LOW_VALUE: 10 }
  max_fixed_discount: 50
messages:
  sms_max_chars: 320
  email_footer_required: true
  banned_phrases: ["guaranteed", "risk free", "act now or lose", "final warning"]
  allowed_cta_url_keys: [store_home, account_page, offers_page]
frequency:
  max_messages_per_customer: 2      # across ALL campaigns
  window_days: 14
analytics:
  attribution_window_days: 14       # a CONVERTED event counts only inside this
  min_purchasers_for_tiering: 20
costs: { email_cost: 0.001, sms_cost: 0.01 }
```

**No `quiet_hours`.** It was specified in two contradictory places, and with no
scheduler there is nowhere to defer a send *to* — the only honest implementations
were "reject the operator's send" or "silently ignore the setting". Cut, and
recorded in the post-MVP list; it returns with the background worker `[RV-A3]`.

**Frequency cap and channel count interact.** The cap is 2 messages per customer per
14 days across all campaigns, and an `EMAIL_SMS` campaign spends both at once. That
is intentional and now reachable, because v1 sends one message per channel and no
follow-ups `[RV-C6]`.

Each violation returns `(rule_id, message, observed, allowed)`. The campaign goes to
`FAILED` with the full list — the system never silently rewrites a 50% discount down
to 20%, because a silently corrected campaign hides a broken prompt or a broken
policy `[FR-38]`.

Enforcement split by timing, which matters:

| Check | When | Why |
|---|---|---|
| Offer cap, message length, footer, banned phrases, placeholders, CTA keys, forbidden literals | Validation | Content properties, fixed at generation |
| Suppression, consent, frequency cap | **Send** `[FR-40]` | State can change between approval and send `[EC-10]` |

**Forbidden literals** `[VR-07]`, `[RV-C11]` — generated content is rejected if it
contains an email address, a phone number, an order number, a name-shaped literal,
a URL outside `allowed_cta_url_keys`, **or a `customer_id`**. The last one matters
most: `customer_id` is the only identifier the model actually receives, so it is the
only one it could plausibly paste into a template.

---

## 8. Rendering `[FR-29]`–`[FR-31]`

```yaml
# config/placeholders.yaml
placeholders:
  first_name:            { source: customer_name, transform: first_token, fallback: "there" }
  value_tier:            { source: value_tier,    fallback: null }
  days_since_purchase:   { source: days_since_purchase, fallback: null }
  last_purchase_category:{ source: last_purchase_category, fallback: null }
  offer_value:           { source: offer.value,   fallback: null }
  offer_code:            { source: offer.code,    fallback: null }
  brand_name:            { source: account.brand_name, fallback: null }
  unsubscribe_url:       { source: system.unsubscribe_url, fallback: null }
```

```python
def render(template: str, ctx: RenderContext) -> str:
    for key in extract_placeholders(template):
        if key not in ALLOWLIST:
            raise TemplateError(f"unknown placeholder: {key}")   # [VR-08]
        value = resolve(key, ctx) or ALLOWLIST[key].fallback
        if value is None:
            raise SkipCustomer(f"unresolved: {key}")             # [FR-31][VR-09]
    ...
```

Fails closed, always. A message with a visible `{{first_name}}` is worse than a
message not sent. Values are channel-escaped before substitution `[SEC-11]`.

Because the model writes the template and code writes the values, fabricating a
customer fact is not something the model can do wrong — it is something it cannot
express `[R1]`.

---

## 9. Communication `[FR-49]`–`[FR-55]`

```python
class EmailProvider(Protocol):
    async def send(self, to: str, subject: str, body: str) -> ProviderResult: ...

class SMSProvider(Protocol):
    async def send(self, to: str, body: str) -> ProviderResult: ...
```

v1 ships `MockEmailProvider` / `MockSMSProvider` (log, deterministic id, configurable
failure rate for exercising retry paths). Selected by `EMAIL_PROVIDER=mock`.

Send loop, per customer:

```
recompute hash over stored content + campaign_targets
  must equal approved hash                  [FR-45] → abort ALL on mismatch
for each row in campaign_targets:           # frozen list; nothing may be added
    check suppression + consent             [FR-40][EC-09]
    check frequency cap                     [FR-54]
    assign variant:
      sha256(campaign_id + channel + customer_id) % n     [FR-53][RV-C9]
    render                                  → SkipCustomer ⇒ record + continue
    send via adapter (retry per policy)     [EH-07]
    write send_log row (SENT | FAILED | SKIPPED + reason) [FR-51]
```

The channel is inside the variant hash so a customer receiving both EMAIL and SMS is
not locked to the same label in both — otherwise the two experiments are correlated
and neither result is clean `[RV-C9]`.

Send-time gates can only **remove** recipients from the frozen list, never add one.
That asymmetry is what lets the approval hash cover the audience without breaking
every time someone unsubscribes `[RV-C3]`.

`UNIQUE(campaign_id, customer_id, channel)` makes a replayed send a no-op. A circuit
breaker opens after N consecutive provider failures `[EH-12]`.

---

## 10. Analytics `[FR-56]`–`[FR-62]`

All arithmetic in Python. The model never sees a division `[FR-11]`.

| Metric | Formula | Denominator zero |
|---|---|---|
| delivery_rate | delivered / sent | `null` |
| open_rate | opened / delivered | `null` |
| click_through_rate | clicked / delivered | `null` |
| click_to_open_rate | clicked / opened | `null` |
| conversion_rate | converted / delivered | `null` |
| unsubscribe_rate | unsubscribed / delivered | `null` |
| bounce_rate | bounced / sent | `null` |
| revenue | Σ `engagement_events.revenue` where CONVERTED **and** `occurred_at ≤ sent_at + attribution_window_days` | 0 |
| campaign_cost | Σ per-message cost + discount liability on conversions + `llm_cost_usd` | 0 |
| gross_attributed_roi | (revenue − cost) / cost | `null` |
| reactivation_rate | converted / contacted, over targets with `was_lapsed = 1` | `null` |

`null` rather than 0 for an empty denominator `[FR-57]` — a 0% open rate and "nobody
was sent anything" are different facts, and conflating them produces confidently
wrong optimization advice.

**Three corrections that keep these numbers honest:**

- **Attribution window** `[RV-B9]`. A conversion counts only if it happened within
  `attribution_window_days` (default 14) of that customer's send. Without a window,
  "revenue" silently accumulates every purchase the customer ever makes and ROI is
  not reproducible.
- **LLM cost is in `campaign_cost`** `[RV-D4]`. At `gpt-5-nano` prices it is ~2% of a
  400-message campaign, so it changes little — but a cost model that omits the
  system's own cost is wrong on principle, and the share grows if a stage is
  upgraded to a larger model.
- **The metric is named `gross_attributed_roi`, not `roi`** `[RV-B8]`. Every response
  carries `"basis": "gross attributed; not incremental — no holdout group"`. Some
  converters would have purchased anyway; without a control arm this number cannot
  separate them, and a bare "ROI" label invites exactly that misreading. Holdout
  groups are PS-05.

**`reactivation_rate` is a genuinely distinct number** `[RV-B6]`. It is measured only
over targets flagged `was_lapsed = 1` at freeze time — customers whose
`days_since_purchase` already exceeded their own `expected_interval_days`. Defined
over *all* contacted customers it was arithmetically identical to `conversion_rate`,
because every targeted customer is at risk by construction: two names for one
number. Restricted to the genuinely lapsed, it answers a different question — did we
win back the ones who had actually drifted?

### A/B significance `[FR-59]`

Two-proportion z-test, no scipy:

```python
def two_proportion_z(c1, n1, c2, n2) -> tuple[float, float]:
    p1, p2 = c1 / n1, c2 / n2
    p = (c1 + c2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0, 1.0
    z = (p1 - p2) / se
    return z, math.erfc(abs(z) / math.sqrt(2))     # two-tailed p
```

Gate: each variant needs `min_sends_per_variant` (default 100) and
`min_conversions_total` (default 10). Verdicts:

| Verdict | Condition | Effect on the agent |
|---|---|---|
| `INSUFFICIENT_DATA` | gate not met | must not name a winner `[FR-60]` |
| `SIGNIFICANT` | p < 0.05 | may name the winner |
| `NO_DIFFERENCE` | gate met, p ≥ 0.05 | must not name a winner `[EC-14]` |

> `ponytail:` z-test on conversion only, no multiple-comparison correction. With
> more than two variants per channel, switch to a chi-square test with a
> Holm correction.

---

## 11. API Contracts `[FR-63]`–`[FR-69]`

| Method | Path | Role | Notes |
|---|---|---|---|
| GET | `/health` | none | service, agent-DB read-only status, app-DB writable, config valid |
| POST | `/agent/query` | operator | `{account_id, query}`; returns answer + tool calls made + grounding |
| POST | `/campaigns` | operator | `{account_id, goal, risk_levels?, value_tiers?, max_customers?}` |
| GET | `/campaigns` | operator | all in-scope accounts, or one via `?account_id=` |
| GET | `/campaigns/{id}` | operator | state, validation results, token usage, stale/UNKNOWN exclusion counts |
| GET | `/campaigns/{id}/customers` | operator | targeted customers — **ids and behaviour only, no PII** `[RV-C8]` |
| GET | `/campaigns/{id}/segments` | operator | segments, predicates, sizes |
| GET | `/campaigns/{id}/strategy` | operator | playbooks, offers, channels, rationale |
| GET | `/campaigns/{id}/messages` | operator | variants + rendered previews `[FR-34]` |
| POST | `/campaigns/{id}/approve` | **approver** | `{note?}` → `APPROVED` |
| POST | `/campaigns/{id}/reject` | **approver** | `{reason}` → `REJECTED` |
| POST | `/campaigns/{id}/cancel` | operator | → `CANCELLED` |
| POST | `/campaigns/{id}/revise` | operator | on `REJECTED`/`FAILED`: clones to a new campaign in `RECEIVED`, carrying the reason as feedback `[FR-48a]` |
| POST | `/campaigns/{id}/send` | operator | requires `APPROVED`; hash re-verified over content **and** audience `[FR-63a]` |
| GET | `/campaigns/{id}/metrics` | operator | campaign / segment / variant metrics |
| GET | `/campaigns/{id}/optimization` | operator | recommendation + statistical verdict |
| POST | `/campaigns/{id}/simulate-events` | operator | **dev only** — generates engagement events. Returns 404 unless `ENV=dev` `[RV-D3]` |

`account_id` is mandatory in the body of `POST /campaigns` and `POST /agent/query`:
400 if omitted, 403 if outside the caller's scope. A key may carry several accounts,
but `ScopedToolset` binds exactly one, so the agent is never constructed with more
than one tenant in reach `[RV-C1]`.

`POST /campaigns/{id}/simulate-events` exists so that all eight demo requests run
through the API. Without it the loop breaks between "send the campaign" and "how did
it perform", where a human would otherwise have to run a script `[RV-D3]`.

Authentication is a global dependency over the whole app with a four-path public
allowlist (`/health`, `/docs`, `/redoc`, `/openapi.json`), not a per-route decorator:
a route added in a later milestone is protected without anyone remembering to say so,
and the opt-in design fails silently in exactly the case that matters. Roles do not
nest — an operator key cannot approve and an approver key cannot create — because
separation of duties is the only reason to have two roles.

Errors are uniform:

```json
{"error": {"code": "POLICY_VIOLATION", "message": "…",
           "details": [{"rule_id": "OFFER_MAX_DISCOUNT",
                        "observed": 50, "allowed": 20}],
           "correlation_id": "…"}}
```

Out-of-scope resources return 404, never a filtered result `[AZ-05]`, `[FR-68]`.

---

## 12. Observability — Grafana Cloud `[FR-17]`, `[NFR-07]`

No local Grafana, Tempo, Prometheus or Alloy. The SDK exports OTLP/HTTP straight to
the Grafana Cloud gateway.

```
FastAPI app ──OTLP/HTTP + Basic auth──► Grafana Cloud OTLP gateway
                                          ├─ traces  → Tempo (hosted)
                                          ├─ metrics → Mimir (hosted)
                                          └─ logs    → Loki  (hosted)
```

```bash
OTEL_SERVICE_NAME=texting-agent
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_EXPORTER_OTLP_ENDPOINT=https://otlp-gateway-<zone>.grafana.net/otlp
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic%20<base64(instanceID:token)>
OTEL_RESOURCE_ATTRIBUTES=service.namespace=retention,deployment.environment=dev
```

Auto-instrumentation: FastAPI, sqlite3, httpx, logging. Manual spans:

```
POST /campaigns                       (root)
├── auth.resolve_scope
├── scoring.score_account             attrs: customers, duration
├── agent.stage.analyze               attrs: model, tokens_in/out, latency
│   └── llm.request
├── agent.stage.segment
├── segmentation.assign               attrs: segments, assigned, dropped
├── serper.search                     (optional; errors do not fail the parent)
├── agent.stage.plan
├── agent.stage.generate
├── validation.pydantic / .business / .policy   attrs: passed, violations
├── approval.await
├── send.batch
│   ├── render.customer
│   └── provider.email.send / provider.sms.send
└── analytics.compute
```

Every `agent.tool.<name>` span carries `tool.name`, `rows_returned`, `duration_ms` —
never customer values `[SEC-07]`.

### Metrics

| Technical | Business |
|---|---|
| `agent_runs_total{stage,status}` | `customers_analyzed_total` |
| `agent_errors_total{stage,code}` | `churn_candidates_total{risk_level}` |
| `agent_stage_latency_seconds` | `campaigns_generated_total{state}` |
| `llm_requests_total{model,stage}` | `customers_contacted_total{channel}` |
| `llm_tokens_total{model,direction}` | `messages_skipped_total{reason}` |
| `llm_latency_seconds` | `conversions_total` |
| `db_queries_total{db,operation}` | `revenue_recovered_total` |
| `db_query_latency_seconds` | `unsubscribes_total` |
| `serper_requests_total{status}` | `policy_violations_total{rule_id}` |
| `email_sent_total` / `email_failed_total` | `approval_decisions_total{decision}` |
| `sms_sent_total` / `sms_failed_total` | |

### Alerts (defined in Grafana Cloud)

| Alert | Condition |
|---|---|
| Email failure rate | `email_failed / email_sent > 0.05` over 15 m |
| SMS failure rate | `sms_failed / sms_sent > 0.05` over 15 m |
| Agent error rate | `agent_errors / agent_runs > 0.10` over 15 m |
| LLM latency | p95 `llm_latency_seconds > 30` over 10 m |
| Serper latency | p95 > 5 s over 10 m |
| Unsubscribe rate | campaign unsubscribe rate > 1% |
| Policy violations | any `policy_violations_total` increase (indicates prompt drift) |
| Token budget | `llm_tokens_total` per campaign above the configured cap |

### Logging `[SEC-07]`, `[AC-19]`

Structured JSON with `timestamp, level, event, trace_id, span_id, request_id,
account_id, campaign_id, agent_run_id, stage, tool_name, status, latency_ms`.
Customer identifiers only — never names, emails, phones or full records. A test
scans emitted logs against seed PII values.

---

## 13. Configuration & Secrets `[SEC-08]`

```bash
# .env.example
OPENAI_API_KEY=                   # supplied by the operator in .env (never committed)
OPENAI_MODEL_ANALYZE=gpt-5-nano
OPENAI_MODEL_SEGMENT=gpt-5-nano
OPENAI_MODEL_PLAN=gpt-5-nano
OPENAI_MODEL_GENERATE=gpt-5-nano
OPENAI_MODEL_OPTIMIZE=gpt-5-nano
OPENAI_MODEL_QUERY=gpt-5-nano
AGENT_TOKEN_BUDGET=60000
AGENT_MAX_TOOL_ITERATIONS=6
ENV=dev                           # gates POST /campaigns/{id}/simulate-events

SERPER_API_KEY=
SERPER_ENABLED=false
SERPER_TIMEOUT_SECONDS=5

AGENT_DB_PATH=./data/customer_agent.db
APP_DB_PATH=./data/app.db

EMAIL_PROVIDER=mock
SMS_PROVIDER=mock
EMAIL_PROVIDER_KEY=
SMS_PROVIDER_KEY=

API_KEYS_JSON='{"key_acct_a":{"account_ids":["ACC_A"],"role":"approver","principal":"ops@a.example"}}'

OTEL_SERVICE_NAME=texting-agent
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_EXPORTER_OTLP_ENDPOINT=
OTEL_EXPORTER_OTLP_HEADERS=
LOG_LEVEL=INFO
```

YAML configs (`scoring`, `playbooks`, `policy`, `placeholders`) are validated by
Pydantic models at startup; invalid config prevents boot `[VR-11]`, `[EH-11]`. Each
carries a `version` recorded on campaigns `[NFR-11]`.

### Playbooks `[FR-23]`

```yaml
# config/playbooks.yaml
version: 1
playbooks:
  VIP_REACTIVATION:
    applies_to_tiers: [VIP, HIGH_VALUE]
    allowed_offer_types: [LOYALTY_POINTS, EARLY_ACCESS, PERCENTAGE_DISCOUNT]
    tone: premium, personal, appreciative
    guidance: Acknowledge tenure and value. Lead with benefit, not discount.
  PRICE_SENSITIVE:
    applies_to_tiers: [STANDARD, LOW_VALUE]
    allowed_offer_types: [PERCENTAGE_DISCOUNT, FIXED_DISCOUNT, FREE_SHIPPING]
    tone: direct, value-focused
    guidance: Lead with the saving and a clear deadline.
  CART_ABANDONMENT:
    allowed_offer_types: [FREE_SHIPPING, PERCENTAGE_DISCOUNT, NONE]
    guidance: Remind of the abandoned intent; incentive is secondary.
  DORMANT:
    allowed_offer_types: [PERCENTAGE_DISCOUNT, NONE]
    guidance: Re-introduce what changed; low friction, single CTA.
  SUPPORT_RECOVERY:
    allowed_offer_types: [NONE, FIXED_DISCOUNT]
    guidance: Acknowledge the issue, offer resolution first, ask for feedback.
```

---

## 14. Error Handling Implementation `[EH-01]`–`[EH-12]`

| Mechanism | Where | Detail |
|---|---|---|
| Retry with backoff | `openai_client`, providers | 3 attempts, exponential + jitter, retry only 429/5xx/timeout |
| Timeouts | all external calls | LLM 60 s, Serper 5 s, providers 10 s |
| Circuit breaker | providers | opens after 5 consecutive failures, 60 s cooldown |
| Graceful degradation | Serper | failure logged, metric incremented, pipeline continues `[EH-06]` |
| Transactional writes | app DB | one transaction per state transition; rollback leaves state untouched |
| Fail-fast config | startup | invalid YAML or missing required env aborts boot |
| Correlation ids | exception handler | client sees `correlation_id`; detail stays in logs `[EH-10]` |
| Telemetry isolation | OTel | exporter failures are swallowed; requests unaffected `[NFR-08]`, `[EC-22]` |

---

## 15. Testing Strategy

| Suite | Covers | Key assertions |
|---|---|---|
| `tests/security/test_db_boundary.py` | `[SEC-01]`, `[SEC-02]` | INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/ATTACH all raise; `sqlite_master` lists exactly one table |
| `tests/security/test_account_isolation.py` | `[SEC-05]`, `[AZ-*]` | Every tool and endpoint under key A returns zero account-B rows; body-supplied `account_id` is ignored |
| `tests/security/test_pii_boundary.py` | `[SEC-06]`, `[SEC-07]` | Captured LLM payloads and emitted logs contain no seed name/email/phone |
| `tests/security/test_prompt_injection.py` | `[SEC-15]` | Adversarial `/agent/query` prompts (scope escape, "run SQL", "list all tables", "ignore instructions") change nothing |
| `tests/security/test_import_boundary.py` | `[SEC-09]` | `app/agent/**` imports neither `app_db` nor provider modules |
| `tests/test_scoring.py` | `[FR-04]`–`[FR-09]` | Known fixtures → exact scores; missing signals renormalise; config change changes output |
| `tests/test_policy_engine.py` | `[FR-37]`, `[FR-38]` | 50% discount rejected with rule id; nothing auto-corrected |
| `tests/test_rendering.py` | `[FR-29]`–`[FR-31]` | Unknown placeholder raises; null without fallback skips; no raw placeholder ever ships |
| `tests/test_approval.py` | `[FR-41]`–`[FR-46]` | Double approve → 409; role gate; hash mismatch aborts send |
| `tests/test_analytics.py` | `[FR-56]`–`[FR-59]` | Rates against hand-computed fixtures; zero denominator → `null`; z-test against known values |
| `tests/test_orchestrator.py` | `[EC-12]`, `[EH-09]` | Illegal transitions rejected; concurrent approval — exactly one wins |
| `tests/test_e2e_loop.py` | `[AC-1]`–`[AC-9]` | Full loop with a stubbed LLM and mock providers |

LLM calls are stubbed by default (recorded structured responses), so the suite is
deterministic, free and offline. One opt-in live smoke test runs against the real
API when `RUN_LIVE_LLM_TESTS=1`.

---

## 16. Deviations from the Original Specification

Rows 1–10 are deviations decided when the specification was first reviewed; rows
11–18 came out of the design review of this document set; rows 19–20 were
raised while implementing M2. Citations of the form
`[RV-xx]` elsewhere in this document refer to that review's finding ids — `RV-A*`
contradictions, `RV-B*` modelling defects, `RV-C*` gaps, `RV-D*` unverified choices.
`RV-M<n>*` ids are findings raised during implementation of that milestone.
They are distinct from the PRD's assumption ids (`[A1]`–`[A9]`).

| # | Specification said | This TRD does | Why |
|---|---|---|---|
| 1 | "The database itself should enforce read-only" | Two database files + `mode=ro` + `query_only` + semantic tools | SQLite has no roles or `GRANT`; a separate file is the only real enforcement, and it maps cleanly onto a Postgres `GRANT SELECT` role later |
| 2 | Store `churn_score`, `days_since_*` as columns | Compute on read | Stored derivations go stale silently; on-read costs ~10 ms at 5k rows |
| 3 | Agent generates personalized messages | Agent generates templates; code renders values | Makes PII fabrication and leakage structurally impossible, cuts tokens ~100×, and makes human approval feasible |
| 4 | 17 orchestrator states incl. `SCHEDULED`, `PAUSED` | 15 states, no scheduling | v1 sends synchronously; a state that can never be entered is dead code |
| 5 | Self-hosted Grafana + Tempo + Alloy | Grafana Cloud over OTLP directly | Same telemetry, no infrastructure to run; app-side instrumentation is identical |
| 6 | Absolute value thresholds implied | Percentile tiers within account | No currency assumption; works across account sizes |
| 7 | `POST /campaigns/{id}/pause` | `cancel` instead | Nothing is running to pause in a synchronous send |
| 8 | Phase 19 "testing + hardening" last | Security tests written alongside each phase | The boundaries are the product; testing them last means building on unverified assumptions |
| 9 | "Agent must never fabricate" as instruction | Instruction **plus** template/render split and content validators | Prompt text is not a control |
| 10 | OpenAI Agents SDK optional | Plain SDK + structured outputs | The orchestrator is already the state machine; a framework adds indirection without capability |
| 11 | Point-in-time engagement fields only | Five prior-period columns added | The spec's own example — "engagement decreased by 70%" — is not computable without history. `ENGAGEMENT_DECLINE` must be able to mean decline |
| 12 | Engagement implied from email metrics | Channel-aware `max()` over email and SMS, each against its own baseline | An SMS-primary customer scored as maximally disengaged was a guaranteed false positive |
| 13 | Value implied as a churn signal | Value removed from the risk score entirely | Double-counting value into risk spends retention budget where its return is worst |
| 14 | Lifetime `cart_abandonment_count`, `support_issue_count` | Trailing-90-day counters | Lifetime counts make the longest-tenured customers permanently max both signals |
| 15 | Approval covers content | Approval hash covers content **and** the frozen audience | Re-scoring between approval and send could otherwise retarget a campaign the approver had already signed off |
| 16 | Quiet hours in policy | Removed | Specified in two contradictory places, and with no scheduler there is nowhere to defer to |
| 17 | ROI as a headline metric | `gross_attributed_roi` + attribution window + LLM cost + explicit "not incremental" basis | Without a holdout the number overstates; an unqualified "ROI" invites that misreading |
| 18 | Model tier unspecified | `gpt-5-nano` on all six call sites, each independently overridable | Cheapest available (~$0.008/campaign); per-stage variables make a quality problem a one-line fix |
| 19 | `min_signals_required` over "signals with data present" | Zero-valued counters score but do not satisfy the gate `[RV-M2a]` | The counters are `NOT NULL DEFAULT 0`, so the literal reading makes `UNKNOWN` unreachable and `[FR-04c]` untestable |
| 20 | Value tier keyed on having purchased | Keyed on `total_orders > 0 AND total_spend > 0` | A missing `last_purchase_at` disables the purchase-gap signal (DQ-04); it does not make the money on the account disappear |
