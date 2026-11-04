"""Generate a realistic synthetic marketing dataset as CSV files.

Why synthetic? Real customer data can't go in a public repo. The generator
builds in real-world patterns (recently active and paying customers engage
more, ads have lower open rates, etc.) so the analytics and agents have
meaningful signal to work with. Swap this step for a Kaggle dataset or a CRM
export later — the ETL only cares about the CSV columns.

Output: customers.csv, campaigns.csv, campaign_events.csv in RAW_DATA_DIR.
"""
import csv
import random
from datetime import date, datetime, timedelta
from pathlib import Path

FIRST_NAMES = [
    "Aarav", "Fatima", "Rohan", "Aisha", "Omar", "Priya", "Liam", "Sara", "Yusuf", "Meera",
    "Noah", "Zara", "Arjun", "Layla", "Ethan", "Ananya", "Hassan", "Emma", "Kabir", "Noor",
]
LOCATIONS = {
    "India": ["Mumbai", "Bengaluru", "Delhi", "Pune", "Bhopal", "Hyderabad"],
    "UAE": ["Dubai", "Abu Dhabi", "Sharjah"],
    "UK": ["London", "Manchester"],
    "USA": ["New York", "Austin", "San Francisco"],
}
COUNTRY_WEIGHTS = [0.45, 0.25, 0.12, 0.18]
INDUSTRIES = ["E-commerce", "SaaS", "Education", "Healthcare", "Manufacturing", "Agency"]
PLANS = ["free", "pro", "business"]
PLAN_WEIGHTS = [0.6, 0.3, 0.1]

# (name, channel, segment description, subject, budget)
CAMPAIGNS = [
    ("Welcome Series - New Signups", "email", "New signups (< 30 days)", "Welcome to Brightloop, {{first_name}}! Your first 3 steps", 300),
    ("Pro Upgrade - Feature Spotlight", "email", "Active free users", "Unlock automated reports with Pro", 500),
    ("Win-back - 60 Day Inactive", "email", "Inactive 60+ days", "We miss you, {{first_name}} — here's what's new", 400),
    ("Diwali Festive Offer", "email", "India customers", "Festive offer: 30% off Pro this Diwali", 800),
    ("Ramadan Productivity Tips", "email", "UAE customers", "Plan smarter this Ramadan with Brightloop", 600),
    ("Webinar Invite - AI Reports", "email", "Business + Pro plans", "Live: build AI reports in 20 minutes", 350),
    ("LinkedIn - Agency Case Study", "social", "Agency industry", "How one agency cut reporting time by 70%", 1200),
    ("Instagram - Product Tour Reel", "social", "All customers", "60-second tour of Brightloop dashboards", 900),
    ("Google Ads - Analytics Software", "ads", "E-commerce + SaaS", "Marketing analytics that writes itself", 2500),
    ("Google Ads - Retargeting Trial", "ads", "Free users", "Your free trial of Pro is waiting", 1800),
    ("Meta Ads - Education Vertical", "ads", "Education industry", "Track student enrolment campaigns easily", 1500),
    ("Black Friday Annual Plan", "email", "All opted-in customers", "Black Friday: 2 months free on annual plans", 1000),
]

# Base funnel rates per channel: (open, click | open, convert | click)
CHANNEL_RATES = {
    "email": (0.32, 0.18, 0.12),
    "social": (0.55, 0.06, 0.08),  # "open" = impression/view for social
    "ads": (0.70, 0.025, 0.10),  # "open" = impression for ads
}


def _customers(rng: random.Random, n: int, today: date) -> list[dict]:
    rows = []
    for i in range(1, n + 1):
        country = rng.choices(list(LOCATIONS), COUNTRY_WEIGHTS)[0]
        plan = rng.choices(PLANS, PLAN_WEIGHTS)[0]
        signup = today - timedelta(days=rng.randint(1, 900))
        # paying customers tend to be more recently active
        max_idle = {"free": 240, "pro": 120, "business": 60}[plan]
        idle = min((today - signup).days, rng.randint(0, max_idle))
        ltv_base = {"free": 0, "pro": 290, "business": 1450}[plan]
        name = rng.choice(FIRST_NAMES)
        rows.append({
            "id": i,
            "email": f"{name.lower()}.{i}@example.com",
            "first_name": name,
            "country": country,
            "city": rng.choice(LOCATIONS[country]),
            "age": rng.randint(21, 62),
            "industry": rng.choice(INDUSTRIES),
            "plan": plan,
            "signup_date": signup.isoformat(),
            "last_active_date": (today - timedelta(days=idle)).isoformat(),
            "lifetime_value": round(ltv_base * rng.uniform(0.5, 2.5), 2),
            "email_opt_in": 1 if rng.random() < 0.88 else 0,
        })
    return rows


def _engagement_boost(c: dict, today: date) -> float:
    idle = (today - date.fromisoformat(c["last_active_date"])).days
    recency = 1.4 if idle < 14 else 1.1 if idle < 45 else 0.75 if idle < 120 else 0.45
    plan = {"free": 0.9, "pro": 1.15, "business": 1.3}[c["plan"]]
    return recency * plan


def generate(out_dir: str, n_customers: int = 2000, seed: int = 42, today: date | None = None) -> dict[str, int]:
    rng = random.Random(seed)
    today = today or date.today()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    customers = _customers(rng, n_customers, today)
    campaigns, events = [], []
    event_id = 1
    for cid, (name, channel, segment, subject, budget) in enumerate(CAMPAIGNS, start=1):
        sent_date = today - timedelta(days=rng.randint(7, 330))
        campaigns.append({
            "id": cid, "name": name, "channel": channel, "segment": segment,
            "subject": subject, "sent_date": sent_date.isoformat(), "budget": budget,
        })
        audience = rng.sample(customers, k=rng.randint(int(n_customers * 0.2), int(n_customers * 0.5)))
        p_open, p_click, p_conv = CHANNEL_RATES[channel]
        personalised = "{{first_name}}" in subject
        for c in audience:
            if channel == "email" and not c["email_opt_in"]:
                continue
            boost = _engagement_boost(c, today) * (1.15 if personalised else 1.0)
            t = datetime.combine(sent_date, datetime.min.time()) + timedelta(hours=rng.randint(8, 11))

            def add(kind: str, revenue: float = 0.0, cid: int = cid, c: dict = c) -> None:
                nonlocal event_id, t
                t = t + timedelta(minutes=rng.randint(1, 600))
                events.append({
                    "id": event_id, "campaign_id": cid, "customer_id": c["id"],
                    "event_type": kind, "occurred_at": t.isoformat(sep=" "), "revenue": revenue,
                })
                event_id += 1

            add("sent")
            if rng.random() < min(p_open * boost, 0.95):
                add("open")
                if rng.random() < min(p_click * boost, 0.9):
                    add("click")
                    if rng.random() < min(p_conv * boost, 0.8):
                        add("convert", round(rng.choice([29, 29, 49, 99, 290, 490]) * rng.uniform(0.9, 1.1), 2))

    for fname, rows in (("customers.csv", customers), ("campaigns.csv", campaigns), ("campaign_events.csv", events)):
        with open(out / fname, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    return {"customers": len(customers), "campaigns": len(campaigns), "events": len(events)}
