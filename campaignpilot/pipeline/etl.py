"""Extract -> validate -> load raw CSVs into the database, then compute metrics.

Design choices worth explaining in interviews:
* Idempotent: each run replaces the warehouse tables inside ONE transaction,
  so re-running never duplicates rows and a failed run leaves old data intact.
* Validation before load: bad rows are rejected and counted, not silently loaded.
* Metrics are derived with SQL (GROUP BY on events) — the same query you'd
  run in a real warehouse.
"""
import logging
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import case, delete, func, select

from campaignpilot.db.models import Campaign, CampaignEvent, CampaignMetrics, Customer
from campaignpilot.db.session import get_session, init_db

log = logging.getLogger(__name__)

VALID_PLANS = {"free", "pro", "business"}
VALID_CHANNELS = {"email", "social", "ads"}
VALID_EVENTS = {"sent", "open", "click", "convert"}


def _read(path: Path, required: list[str]) -> pd.DataFrame:
    df = pd.read_csv(path)
    missing = set(required) - set(df.columns)
    if missing:
        raise ValueError(f"{path.name} is missing columns: {sorted(missing)}")
    return df


def extract_and_validate(raw_dir: str) -> tuple[dict[str, pd.DataFrame], dict[str, int]]:
    raw = Path(raw_dir)
    customers = _read(raw / "customers.csv", ["id", "email", "plan", "country", "last_active_date"])
    campaigns = _read(raw / "campaigns.csv", ["id", "name", "channel", "budget"])
    events = _read(raw / "campaign_events.csv", ["id", "campaign_id", "customer_id", "event_type"])

    before = {"customers": len(customers), "campaigns": len(campaigns), "events": len(events)}

    customers = customers.dropna(subset=["id", "email"]).drop_duplicates("id")
    customers = customers[customers["plan"].isin(VALID_PLANS)]
    campaigns = campaigns[campaigns["channel"].isin(VALID_CHANNELS)].drop_duplicates("id")
    events = events[events["event_type"].isin(VALID_EVENTS)]
    # referential integrity: drop events pointing at unknown customers/campaigns
    events = events[events["customer_id"].isin(customers["id"]) & events["campaign_id"].isin(campaigns["id"])]
    events = events.drop_duplicates("id")

    rejected = {
        "customers": before["customers"] - len(customers),
        "campaigns": before["campaigns"] - len(campaigns),
        "events": before["events"] - len(events),
    }
    return {"customers": customers, "campaigns": campaigns, "events": events}, rejected


def _records(df: pd.DataFrame, date_cols=(), datetime_cols=()) -> list[dict]:
    df = df.copy()
    for col in date_cols:
        df[col] = df[col].map(date.fromisoformat)
    for col in datetime_cols:
        df[col] = df[col].map(datetime.fromisoformat)
    return df.to_dict(orient="records")


def compute_metrics(session) -> int:
    """Aggregate funnel metrics per campaign from raw events."""
    e = CampaignEvent
    counts = (
        select(
            e.campaign_id,
            func.sum(case((e.event_type == "sent", 1), else_=0)).label("sent"),
            func.sum(case((e.event_type == "open", 1), else_=0)).label("opens"),
            func.sum(case((e.event_type == "click", 1), else_=0)).label("clicks"),
            func.sum(case((e.event_type == "convert", 1), else_=0)).label("conversions"),
            func.coalesce(func.sum(e.revenue), 0).label("revenue"),
        )
        .group_by(e.campaign_id)
        .subquery()
    )
    rows = session.execute(
        select(Campaign.id, Campaign.channel, Campaign.budget, counts).join(counts, counts.c.campaign_id == Campaign.id)
    ).all()

    session.execute(delete(CampaignMetrics))
    now = datetime.now()
    for r in rows:
        sent, opens, clicks, conv = int(r.sent), int(r.opens), int(r.clicks), int(r.conversions)
        session.add(CampaignMetrics(
            campaign_id=r.id, channel=r.channel, sent=sent, opens=opens, clicks=clicks,
            conversions=conv, revenue=round(float(r.revenue), 2),
            open_rate=round(opens / sent, 4) if sent else 0.0,
            ctr=round(clicks / sent, 4) if sent else 0.0,
            conversion_rate=round(conv / clicks, 4) if clicks else 0.0,
            roas=round(float(r.revenue) / r.budget, 2) if r.budget else 0.0,
            computed_at=now,
        ))
    return len(rows)


def load(frames: dict[str, pd.DataFrame]) -> int:
    init_db()
    with get_session() as s:  # one transaction for the whole refresh
        for model in (CampaignMetrics, CampaignEvent, Campaign, Customer):
            s.execute(delete(model))
        s.bulk_insert_mappings(Customer, _records(frames["customers"], ["signup_date", "last_active_date"]))
        s.bulk_insert_mappings(Campaign, _records(frames["campaigns"], ["sent_date"]))
        s.bulk_insert_mappings(CampaignEvent, _records(frames["events"], datetime_cols=["occurred_at"]))
        s.flush()
        return compute_metrics(s)


def run_etl(raw_dir: str) -> dict:
    frames, rejected = extract_and_validate(raw_dir)
    n_metrics = load(frames)
    summary = {
        "loaded": {k: len(v) for k, v in frames.items()},
        "rejected": rejected,
        "campaign_metrics": n_metrics,
    }
    log.info("ETL complete: %s", summary)
    return summary
