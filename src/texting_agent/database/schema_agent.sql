-- The ONLY table the agent can reach. This file must never gain a second table:
-- physical isolation is the outermost layer of the read-only guarantee (SEC-02).

CREATE TABLE IF NOT EXISTS customer_agent_records (
    account_id                 TEXT    NOT NULL,
    customer_id                TEXT    NOT NULL,

    -- PII: leaves the repository only for rendering and sending. Never a prompt.
    customer_name              TEXT,
    email                      TEXT,
    phone                      TEXT,

    customer_status            TEXT    NOT NULL DEFAULT 'ACTIVE',
    registration_date          TEXT    NOT NULL,

    last_activity_at           TEXT,
    last_login_at              TEXT,
    last_purchase_at           TEXT,

    total_orders               INTEGER NOT NULL DEFAULT 0,
    total_spend                REAL    NOT NULL DEFAULT 0,
    average_order_value        REAL,
    purchase_frequency_days    REAL,

    -- current window: trailing 90 days from data_as_of
    email_open_rate            REAL,
    email_click_rate           REAL,
    sms_response_rate          REAL,
    orders_last_90d            INTEGER NOT NULL DEFAULT 0,
    cart_abandonment_count_90d INTEGER NOT NULL DEFAULT 0,
    support_issue_count_90d    INTEGER NOT NULL DEFAULT 0,

    -- prior window: the 90 days before that. Basis for every trend signal.
    email_open_rate_prev_90d   REAL,
    sms_response_rate_prev_90d REAL,
    orders_prev_90d            INTEGER NOT NULL DEFAULT 0,

    preferred_channel          TEXT,
    email_consent              INTEGER NOT NULL DEFAULT 0,
    sms_consent                INTEGER NOT NULL DEFAULT 0,
    last_purchase_category     TEXT,
    data_as_of                 TEXT    NOT NULL,

    PRIMARY KEY (account_id, customer_id),
    CHECK (total_orders >= 0 AND total_spend >= 0),
    CHECK (orders_last_90d >= 0 AND orders_prev_90d >= 0),
    CHECK (email_consent IN (0, 1) AND sms_consent IN (0, 1))
);

CREATE INDEX IF NOT EXISTS idx_car_account_status
    ON customer_agent_records (account_id, customer_status);
