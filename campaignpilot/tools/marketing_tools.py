"""The marketing tools agents are allowed to use.

These plain functions are the single source of truth: the MCP server exposes
them over the Model Context Protocol, and DirectToolClient calls them
in-process. Agents never get raw database access — only these guarded tools.
"""
from __future__ import annotations

import re

from sqlalchemy import text

from campaignpilot.db.session import get_engine
from campaignpilot.rag.store import search

ALLOWED_TABLES = {"customers", "campaigns", "campaign_events", "campaign_metrics"}
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|truncate|attach|detach|pragma|grant|revoke|copy|vacuum|exec|execute)\b",
    re.IGNORECASE,
)
MAX_SAMPLE = 20

SCHEMA_DOC = """Tables (SQLite/PostgreSQL compatible SQL; dates are ISO 'YYYY-MM-DD'):

customers(id INT PK, email TEXT, first_name TEXT, country TEXT, city TEXT, age INT,
          industry TEXT, plan TEXT, signup_date DATE, last_active_date DATE,
          lifetime_value FLOAT, email_opt_in INT)
  country values:  'India', 'UAE', 'UK', 'USA'
  plan values:     'free', 'pro', 'business'
  industry values: 'E-commerce', 'SaaS', 'Education', 'Healthcare', 'Manufacturing', 'Agency'
  email_opt_in:    1 = consented to marketing email, 0 = not

campaigns(id INT PK, name TEXT, channel TEXT('email'|'social'|'ads'), segment TEXT,
          subject TEXT, sent_date DATE, budget FLOAT)
campaign_events(id INT PK, campaign_id INT FK, customer_id INT FK,
                event_type TEXT('sent'|'open'|'click'|'convert'), occurred_at DATETIME, revenue FLOAT)
campaign_metrics(campaign_id INT PK, channel TEXT, sent INT, opens INT, clicks INT, conversions INT,
                 revenue FLOAT, open_rate FLOAT, ctr FLOAT, conversion_rate FLOAT, roas FLOAT)

Relative dates: use :today, a bound parameter holding today's date as 'YYYY-MM-DD'.
  SQLite:     last_active_date >= date(:today, '-14 day')
  PostgreSQL: last_active_date >= CAST(:today AS DATE) - INTERVAL '14 days'
"""


class UnsafeQueryError(ValueError):
    pass


def validate_select(sql: str) -> str:
    """Allow exactly one read-only SELECT over known tables."""
    cleaned = sql.strip().rstrip(";").strip()
    if ";" in cleaned:
        raise UnsafeQueryError("Only a single statement is allowed.")
    if not re.match(r"^(select|with)\b", cleaned, re.IGNORECASE):
        raise UnsafeQueryError("Query must start with SELECT or WITH.")
    if FORBIDDEN.search(cleaned) or "--" in cleaned or "/*" in cleaned:
        raise UnsafeQueryError("Query contains a forbidden keyword or comment.")
    referenced = {t.lower() for t in re.findall(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_]*)", cleaned, re.IGNORECASE)}
    unknown = referenced - ALLOWED_TABLES
    if unknown:
        raise UnsafeQueryError(f"Unknown or disallowed tables: {sorted(unknown)}")
    if "customers" not in referenced:
        raise UnsafeQueryError("A segment query must select from the customers table.")
    return cleaned


def _today() -> str:
    from datetime import date

    return date.today().isoformat()


def get_database_schema() -> str:
    """Describe the marketing database tables, columns and allowed values."""
    dialect = get_engine().dialect.name
    return f"Database dialect: {dialect}\n\n{SCHEMA_DOC}"


def run_segment_query(sql: str, sample_size: int = 5) -> dict:
    """Run a read-only SELECT that returns customer rows. Returns the segment
    size and a small sample. Results always exclude nothing silently; the
    caller decides on opt-in filtering."""
    query = validate_select(sql)
    sample_size = max(1, min(sample_size, MAX_SAMPLE))
    params = {"today": _today()}
    engine = get_engine()
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            if engine.dialect.name == "postgresql":
                conn.execute(text("SET TRANSACTION READ ONLY"))
            count = conn.execute(text(f"SELECT COUNT(*) FROM ({query}) AS seg"), params).scalar_one()
            rows = conn.execute(text(f"SELECT * FROM ({query}) AS seg LIMIT {sample_size}"), params).mappings().all()
            columns = list(rows[0].keys()) if rows else []
            if rows and "id" not in columns:
                raise UnsafeQueryError("Segment query must return the customer 'id' column.")
            sample = [{k: (str(v) if v is not None and not isinstance(v, (int, float, str)) else v) for k, v in r.items()} for r in rows]
        finally:
            trans.rollback()  # never commit anything from agent-written SQL
    return {"sql": query, "count": int(count), "columns": columns, "sample": sample}


def get_campaign_performance(channel: str | None = None, top: int = 5) -> list[dict]:
    """Past campaigns with funnel metrics, best conversion rate first."""
    top = max(1, min(int(top), 20))
    where, params = "", {"top": top}
    if channel:
        where, params["channel"] = "WHERE c.channel = :channel", channel
    sql = f"""
        SELECT c.id, c.name, c.channel, c.segment, c.subject, m.sent, m.open_rate, m.ctr,
               m.conversion_rate, m.conversions, m.revenue, m.roas
        FROM campaign_metrics m JOIN campaigns c ON c.id = m.campaign_id
        {where}
        ORDER BY m.conversions * 1.0 / NULLIF(m.sent, 0) DESC
        LIMIT :top
    """
    with get_engine().connect() as conn:
        rows = conn.execute(text(sql), params).mappings().all()
    return [dict(r) for r in rows]


def get_channel_benchmarks() -> list[dict]:
    """Average open rate, CTR, conversion rate and ROAS per channel."""
    sql = """
        SELECT channel, COUNT(*) AS campaigns, AVG(open_rate) AS avg_open_rate, AVG(ctr) AS avg_ctr,
               AVG(conversion_rate) AS avg_conversion_rate, AVG(roas) AS avg_roas,
               SUM(revenue) AS total_revenue
        FROM campaign_metrics GROUP BY channel ORDER BY channel
    """
    with get_engine().connect() as conn:
        rows = conn.execute(text(sql)).mappings().all()
    digits = {"avg_open_rate": 4, "avg_ctr": 4, "avg_conversion_rate": 4, "avg_roas": 2, "total_revenue": 2}
    return [
        {k: (round(float(v), digits[k]) if k in digits and v is not None else v) for k, v in r.items()}
        for r in rows
    ]


def search_marketing_knowledge(query: str, k: int = 4) -> list[dict]:
    """Semantic search over brand guidelines, products and past campaign learnings."""
    return search(query, k=max(1, min(k, 8)))
