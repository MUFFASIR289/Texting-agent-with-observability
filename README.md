# Texting Agent

AI churn prevention and customer retention agent. A single LLM agent identifies
customers at risk of churning, works out why, plans a differentiated retention
campaign, writes the email and SMS content, and improves the next campaign from
measured results — with every step that must be *correct* rather than *plausible*
handled by deterministic code.

## Quickstart

```bash
uv sync                             # create the venv and install
cp .env.example .env                # then fill in your keys
uv run python scripts/seed_data.py  # generate the synthetic customer database
(cd web && npm install && npm run build)   # build the UI, once
uv run texting-agent                # start everything
```

Then open **http://127.0.0.1:8000**. One command, one port:

| Path | |
|---|---|
| `/` | Landing page |
| `/console` | Operator console — campaigns, approval, send log, ask the agent |
| `/api/...` | The API, with its schema at `/api/docs` |

The console reads `API_KEYS` from the same `.env` the service reads, so there is
nothing to sign into.

The API is Python and the UI is Node, so there are two processes. Only one is
reachable: the API listens on `127.0.0.1:8001` for the UI to call over the
loopback and is published to the browser at `/api` on the public port. Set
`SERVE_UI=false` to run the API by itself on port 8000, as it was before.

Check it:

```bash
curl http://127.0.0.1:8000/api/health
```

Run the tests:

```bash
uv run pytest                              # offline, no keys needed
RUN_LIVE_SMOKE=1 uv run pytest tests/test_live_smoke.py   # opt-in, costs money
```

## Configuration

All settings come from environment variables or `.env`. See
[`.env.example`](.env.example) for the full list. `.env` is gitignored and never
committed.

`OPENAI_API_KEY` is only required from milestone M5 onward; everything before that
runs without it.

## Documentation

The full plan lives in [`claude/`](claude/):

| Document | What it covers |
|---|---|
| [PRD](claude/PRD.md) | Problem, users, goals, MVP scope, success metrics, acceptance criteria |
| [SRS](claude/SRS.md) | Every requirement with an id and a verification method |
| [TRD](claude/TRD.md) | Technical design: schemas, scoring, agent contracts, telemetry |
| [Architecture](claude/ARCHITECTURE.md) | Components, data flow, security model, ADRs |
| [Development Plan](claude/DEVELOPMENT_PLAN.md) | Milestones M0–M11 with definitions of done |
| [UI/UX](claude/UIUX.md) | Design system and build plan for the landing page and console |

Runtime behaviour is tuned in [`config/`](config/): `scoring.yaml` holds the signal
weights, normalisation horizons and risk thresholds; `playbooks.yaml` bounds what the
model may offer each value tier. Both are validated at startup, so a bad value is a
failed boot rather than a bad campaign.

## Security model

Five constraints hold throughout, enforced by mechanism rather than by prompt text:

1. **One agent.** No sub-agents, no delegation.
2. **One table, read-only.** The agent's database file contains exactly
   `customer_agent_records`, opened `mode=ro` with `PRAGMA query_only`. It is never
   given SQL.
3. **No account choice.** `account_id` is bound by the orchestrator; the model has no
   parameter to name an account with.
4. **No PII in prompts.** The model writes templates; code renders customer values.
5. **Deterministic code owns correctness.** Scoring, tiering, segment assignment,
   percentages and policy are Python. The LLM interprets; it never computes.

## Status

**M0 (Foundation)** complete: service skeleton, settings, structured JSON logging,
correlation ids, health endpoint.

**M1 (Data & Security Boundary)** complete: the two database files, the read-only
agent connection, the scoped customer repository, the seed script, and the security
suite that proves the fence — write and DDL rejection, ATTACH rejection, exactly one
table, no unscoped query, and an AST scan for stray or interpolated SQL. `/health`
re-checks the boundary on every call and reports `degraded` rather than `ok` if it
does not hold.

**M2 (Deterministic Intelligence)** complete: seven-signal churn scoring with
renormalisation over available signals, channel-aware engagement trend, reason codes
with evidence, percentile value tiering, and playbook config — all driven by
`config/scoring.yaml` and `config/playbooks.yaml`, both validated at startup. No LLM
is involved in any of it.

**M3 (Agent Tools & Contracts)** complete: `ScopedToolset` with the account bound
at construction, four tools whose every parameter is an enum or a bounded integer,
`CustomerFacts` as the only shape that can reach a prompt, and structured tool errors
that never carry a stack trace, a SQL string or a path. The PII test runs against the
seeded database and fails rather than skips if it is missing.

**M4 (API & Auth)** partially complete: API-key authentication as a global
dependency with constant-time comparison, an immutable per-request scope context,
role gating, per-key rate limiting, and one error envelope carrying the same
correlation id as the `X-Request-ID` header. The routes themselves land in M5,
because every one of them needs a campaign or the agent to exist first.

**M5 (Agent Core & Orchestrator)** complete: one `TextingAgent` class (asserted by
an AST scan over the whole source tree), an LLM client with jittered retries on
transient failures only, one schema re-ask, and a hard token budget checked before
each call; the thirteen-state machine with conditional-UPDATE transitions; priority-
ordered segment assignment; and the API routes that M4's machinery protects. The
adversarial suite scripts a fully compromised model and asserts every escape fails.

Everything runs offline: the agent depends on a Protocol, so the test suite replaces
the model with a stub rather than patching a client library. The one live test costs
money and is opt-in via `RUN_LIVE_SMOKE=1`.

**M6 (Strategy & Content)** complete: PLAN and GENERATE run once per surviving
segment, a placeholder allowlist in `config/placeholders.yaml`, and a renderer that
fails closed — an unknown placeholder fails the campaign, an unresolvable one skips
that customer with a recorded reason. `GET /campaigns/{id}/messages` previews each
variant against a real targeted customer.

**M7 (Validation, Policy & Approval)** complete: offer caps per value tier, banned
phrases, CTA-key and placeholder allowlists, and forbidden-literal checks including
`customer_id`. Violations are reported with rule ids and never corrected. The
audience is frozen into `campaign_targets` and the SHA-256 hash covers content,
offer and audience together, so nothing can change under an approval. The pipeline
stops at `AWAITING_APPROVAL`; approve, reject, cancel and revise are role-gated and
state-guarded.

**M8 (Communication & Sending)** complete: the approved hash is re-verified over
content and the frozen audience before anything is dispatched, and a mismatch aborts
the whole send. Suppression, consent and a cross-campaign frequency cap are enforced
at send time and can only remove recipients. Every attempt writes a `send_log` row
with its outcome and reason. A dev-only event simulator closes the loop so analytics
has something to measure.

Verified end to end on the seeded 5,000-customer account: 4,844 targetable, 4,567
sent, 277 skipped for consent, none failed.

Next: **M9 (Analytics, A/B & Optimization)** — the rates, the two-proportion z-test
and the OPTIMIZE stage.
