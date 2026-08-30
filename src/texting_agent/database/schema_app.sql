-- Operational state. Lives in a SEPARATE FILE from customer_agent_records so the
-- agent's read-only connection cannot see any of it, even if the tool layer failed.

CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id          TEXT PRIMARY KEY,
    account_id           TEXT NOT NULL,
    state                TEXT NOT NULL,
    goal                 TEXT,
    created_by           TEXT NOT NULL,
    created_at           TEXT NOT NULL,
    updated_at           TEXT,
    content_hash         TEXT,            -- content + offer + frozen audience
    model_id             TEXT,
    prompt_version       TEXT,
    config_version       TEXT,
    tokens_in            INTEGER,
    tokens_out           INTEGER,
    llm_cost_usd         REAL,
    excluded_stale_count INTEGER NOT NULL DEFAULT 0,
    excluded_unknown_count INTEGER NOT NULL DEFAULT 0,
    revised_from         TEXT,
    failure_code         TEXT,
    failure_detail       TEXT
);

CREATE TABLE IF NOT EXISTS campaign_segments (
    segment_id     TEXT PRIMARY KEY,
    campaign_id    TEXT NOT NULL REFERENCES campaigns (campaign_id),
    name           TEXT NOT NULL,
    priority       INTEGER NOT NULL,
    predicate_json TEXT NOT NULL,         -- structured predicate, never SQL
    playbook_id    TEXT NOT NULL,
    offer_json     TEXT NOT NULL,
    channels       TEXT NOT NULL,
    customer_count INTEGER NOT NULL,
    rationale      TEXT
);

-- The frozen audience. Written at VALIDATED, before the hash is computed.
-- Send-time gates may SKIP rows here; nothing may ever ADD one.
CREATE TABLE IF NOT EXISTS campaign_targets (
    campaign_id TEXT NOT NULL REFERENCES campaigns (campaign_id),
    segment_id  TEXT NOT NULL,
    account_id  TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    was_lapsed  INTEGER NOT NULL,         -- snapshot for reactivation_rate
    PRIMARY KEY (campaign_id, customer_id)
);

CREATE TABLE IF NOT EXISTS message_variants (
    variant_id       TEXT PRIMARY KEY,
    segment_id       TEXT NOT NULL REFERENCES campaign_segments (segment_id),
    channel          TEXT NOT NULL,
    label            TEXT NOT NULL,
    subject_template TEXT,
    body_template    TEXT NOT NULL,
    cta_text         TEXT,
    cta_url_key      TEXT
);

CREATE TABLE IF NOT EXISTS campaign_approvals (
    campaign_id  TEXT NOT NULL REFERENCES campaigns (campaign_id),
    decision     TEXT NOT NULL,
    approver_id  TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    reason       TEXT,
    decided_at   TEXT NOT NULL,
    PRIMARY KEY (campaign_id, decided_at)
);

CREATE TABLE IF NOT EXISTS send_log (
    send_id             TEXT PRIMARY KEY,
    campaign_id         TEXT NOT NULL REFERENCES campaigns (campaign_id),
    segment_id          TEXT NOT NULL,
    variant_id          TEXT,
    account_id          TEXT NOT NULL,
    customer_id         TEXT NOT NULL,
    channel             TEXT NOT NULL,
    status              TEXT NOT NULL,    -- SENT | FAILED | SKIPPED
    skip_reason         TEXT,
    provider_message_id TEXT,
    error               TEXT,
    attempted_at        TEXT NOT NULL,
    UNIQUE (campaign_id, customer_id, channel)   -- replayed sends are a no-op
);

CREATE TABLE IF NOT EXISTS engagement_events (
    event_id    TEXT PRIMARY KEY,
    send_id     TEXT NOT NULL REFERENCES send_log (send_id),
    event_type  TEXT NOT NULL,
    revenue     REAL,
    occurred_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS suppressions (
    account_id  TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    channel     TEXT NOT NULL,
    reason      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (account_id, customer_id, channel)
);

CREATE TABLE IF NOT EXISTS agent_runs (
    run_id     TEXT PRIMARY KEY,
    campaign_id TEXT,
    account_id TEXT NOT NULL,
    stage      TEXT NOT NULL,
    model_id   TEXT NOT NULL,
    tokens_in  INTEGER,
    tokens_out INTEGER,
    latency_ms INTEGER,
    status     TEXT NOT NULL,
    error      TEXT,
    trace_id   TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_send_log_customer
    ON send_log (account_id, customer_id, attempted_at);
CREATE INDEX IF NOT EXISTS idx_campaigns_account ON campaigns (account_id, state);
