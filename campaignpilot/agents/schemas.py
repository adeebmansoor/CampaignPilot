"""Pydantic schemas the LLM must return (structured JSON output).

Using schemas instead of free text means every agent hands the next one
validated data, and malformed LLM output fails loudly at the boundary.
"""
from typing import Literal

from pydantic import BaseModel, Field

Channel = Literal["email", "social", "ads"]


class CampaignBrief(BaseModel):
    """Planner output: the goal turned into a concrete brief."""
    objective: str = Field(description="One-sentence business objective, e.g. 'convert active free users to Pro'")
    channel: Channel
    audience_description: str = Field(description="Who to target, in plain English")
    key_message: str = Field(description="The single most important message")
    offer: str = Field(default="", description="Discount or offer, if the goal mentions one")
    kpi: str = Field(description="Primary metric to judge success, e.g. 'conversion rate'")


class SegmentQuery(BaseModel):
    """Segmentation agent output."""
    sql: str = Field(description="A single SQLite/PostgreSQL SELECT over the customers table returning customer rows")
    rationale: str = Field(description="Why this segment matches the brief")


class ContentVariant(BaseModel):
    label: str = Field(description="Short name for the angle, e.g. 'Outcome-led'")
    subject_or_headline: str = Field(description="Email subject, social hook, or ad headline")
    preview_text: str = Field(default="", description="Email preview text or ad description")
    body: str = Field(description="Main copy. Use {{first_name}} for personalisation where allowed")
    cta: str = Field(description="Call to action text")
    hashtags: list[str] = Field(default_factory=list)


class CampaignContent(BaseModel):
    """Content agent output: two A/B variants."""
    variants: list[ContentVariant] = Field(min_length=1, max_length=3)
    send_time_recommendation: str = Field(default="", description="Suggested send time with a reason")


class CampaignReview(BaseModel):
    """Reviewer agent output."""
    score: int = Field(ge=1, le=10, description="Overall quality and brand fit, 1-10")
    issues: list[str] = Field(default_factory=list, description="Concrete problems that must be fixed")
    suggestions: list[str] = Field(default_factory=list, description="Optional improvements")
