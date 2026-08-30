"""The only module in the system that writes customer SQL.

Two invariants, both covered by tests in tests/security/:

* Every statement is a complete, static string defined at import time. Nothing is
  built from caller input, so there is no interpolation path to inject through.
* `account_id` is a required argument on every public method and is bound as a
  query parameter. There is no default and no "all accounts" query (SEC-05).
"""

import sqlite3

from texting_agent.schemas.customer import CustomerRecord

_COLUMNS = """
    account_id, customer_id, customer_name, email, phone,
    customer_status, registration_date,
    last_activity_at, last_login_at, last_purchase_at,
    total_orders, total_spend, average_order_value, purchase_frequency_days,
    email_open_rate, email_click_rate, sms_response_rate,
    orders_last_90d, cart_abandonment_count_90d, support_issue_count_90d,
    email_open_rate_prev_90d, sms_response_rate_prev_90d, orders_prev_90d,
    preferred_channel, email_consent, sms_consent,
    last_purchase_category, data_as_of
"""

_FROM_SCOPED = " FROM customer_agent_records WHERE account_id = :account_id"

# Complete statements, fixed at import. Methods select one by key; they never
# assemble SQL at call time.
_SQL: dict[str, str] = {
    "list": "SELECT" + _COLUMNS + _FROM_SCOPED + " ORDER BY customer_id",
    "get": "SELECT" + _COLUMNS + _FROM_SCOPED + " AND customer_id = :customer_id",
    "count": "SELECT COUNT(*) AS n" + _FROM_SCOPED,
    "accounts": "SELECT DISTINCT account_id FROM customer_agent_records ORDER BY account_id",
}


class CustomerRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def _scoped(self, key: str, account_id: str, **params: object) -> sqlite3.Cursor:
        if not account_id or not isinstance(account_id, str):
            raise ValueError("account_id is required and must be a non-empty string")
        return self._conn.execute(_SQL[key], {"account_id": account_id, **params})

    def list_for_account(self, account_id: str) -> list[CustomerRecord]:
        rows = self._scoped("list", account_id).fetchall()
        return [CustomerRecord.model_validate(dict(r)) for r in rows]

    def get(self, account_id: str, customer_id: str) -> CustomerRecord | None:
        row = self._scoped("get", account_id, customer_id=customer_id).fetchone()
        return CustomerRecord.model_validate(dict(row)) if row else None

    def count(self, account_id: str) -> int:
        return int(self._scoped("count", account_id).fetchone()["n"])

    def known_accounts(self) -> list[str]:
        """Administrative only. Never exposed to the agent or to an API caller;
        used by the seed script and by tests to enumerate fixtures."""
        return [r["account_id"] for r in self._conn.execute(_SQL["accounts"]).fetchall()]
