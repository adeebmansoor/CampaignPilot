"""SQLAlchemy models for the marketing warehouse.

customers ──< campaign_events >── campaigns ──1 campaign_metrics
generated_campaigns stores every campaign the agents produced.
"""
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    first_name: Mapped[str] = mapped_column(String(80))
    country: Mapped[str] = mapped_column(String(60), index=True)
    city: Mapped[str] = mapped_column(String(80))
    age: Mapped[int] = mapped_column(Integer)
    industry: Mapped[str] = mapped_column(String(60), index=True)
    plan: Mapped[str] = mapped_column(String(20), index=True)  # free | pro | business
    signup_date: Mapped[date] = mapped_column(Date)
    last_active_date: Mapped[date] = mapped_column(Date, index=True)
    lifetime_value: Mapped[float] = mapped_column(Float)
    email_opt_in: Mapped[int] = mapped_column(Integer, default=1)  # 1 = consented


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    channel: Mapped[str] = mapped_column(String(20), index=True)  # email | social | ads
    segment: Mapped[str] = mapped_column(String(160))
    subject: Mapped[str] = mapped_column(String(255))
    sent_date: Mapped[date] = mapped_column(Date)
    budget: Mapped[float] = mapped_column(Float)


class CampaignEvent(Base):
    __tablename__ = "campaign_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), index=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(20), index=True)  # sent|open|click|convert
    occurred_at: Mapped[datetime] = mapped_column(DateTime)
    revenue: Mapped[float] = mapped_column(Float, default=0.0)


class CampaignMetrics(Base):
    __tablename__ = "campaign_metrics"

    campaign_id: Mapped[int] = mapped_column(ForeignKey("campaigns.id"), primary_key=True)
    channel: Mapped[str] = mapped_column(String(20))
    sent: Mapped[int] = mapped_column(Integer)
    opens: Mapped[int] = mapped_column(Integer)
    clicks: Mapped[int] = mapped_column(Integer)
    conversions: Mapped[int] = mapped_column(Integer)
    revenue: Mapped[float] = mapped_column(Float)
    open_rate: Mapped[float] = mapped_column(Float)
    ctr: Mapped[float] = mapped_column(Float)  # clicks / sent
    conversion_rate: Mapped[float] = mapped_column(Float)  # conversions / clicks
    roas: Mapped[float] = mapped_column(Float)  # revenue / budget
    computed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class GeneratedCampaign(Base):
    __tablename__ = "generated_campaigns"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    goal: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))  # approved | rejected
    segment_sql: Mapped[str] = mapped_column(Text, default="")
    segment_size: Mapped[int] = mapped_column(Integer, default=0)
    content: Mapped[dict] = mapped_column(JSON, default=dict)
    review: Mapped[dict] = mapped_column(JSON, default=dict)
    revisions: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
