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
uv run texting-agent                # start the service on http://127.0.0.1:8000
```

Check it:

```bash
curl http://127.0.0.1:8000/health
```

Run the tests:

```bash
uv run pytest
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

Next: **M4 (API & Auth)** — a secured HTTP surface over the deterministic
intelligence, still with no LLM.
