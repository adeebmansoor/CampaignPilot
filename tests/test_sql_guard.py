import pytest

from campaignpilot.tools.marketing_tools import UnsafeQueryError, run_segment_query, validate_select


@pytest.mark.parametrize("sql", [
    "DELETE FROM customers",
    "SELECT * FROM customers; DROP TABLE customers",
    "UPDATE customers SET plan = 'pro'",
    "SELECT * FROM customers -- comment",
    "SELECT * FROM sqlite_master",
    "SELECT * FROM campaigns",  # segments must come from customers
    "PRAGMA table_info(customers)",
])
def test_unsafe_queries_are_blocked(sql):
    with pytest.raises(UnsafeQueryError):
        validate_select(sql)


def test_valid_segment_query_returns_count_and_sample():
    r = run_segment_query("SELECT * FROM customers WHERE plan = 'business' AND email_opt_in = 1", sample_size=3)
    assert r["count"] > 0
    assert len(r["sample"]) == 3
    assert all(row["plan"] == "business" for row in r["sample"])


def test_relative_dates_use_today_param():
    r = run_segment_query("SELECT * FROM customers WHERE last_active_date >= date(:today, '-7 day')")
    assert r["count"] >= 0


def test_query_cannot_modify_data():
    before = run_segment_query("SELECT * FROM customers")["count"]
    with pytest.raises(UnsafeQueryError):
        run_segment_query("WITH x AS (SELECT 1) DELETE FROM customers")
    assert run_segment_query("SELECT * FROM customers")["count"] == before
