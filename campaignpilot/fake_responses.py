"""Deterministic responses for LLM_PROVIDER=fake.

Lets the whole workflow run in CI and offline demos without an API key.
The first content draft deliberately contains a brand violation so the
reviewer -> writer revision loop is exercised every time.
"""
import re


def _channel(text: str) -> str:
    t = text.lower()
    m = re.search(r"marketer wants this channel: (email|social|ads)", t)
    if m:
        return m.group(1)
    if any(w in t for w in ("linkedin", "instagram", "social")):
        return "social"
    if any(w in t for w in ("google ads", "meta ads", " ad ", " ads", "paid")):
        return "ads"
    return "email"


def _brief(user: str) -> dict:
    t = user.lower()
    channel = _channel(user)
    if "inactive" in t or "win back" in t or "win-back" in t:
        audience, message, kpi = "Customers inactive for 60+ days", "See what's new and get a ready-made dashboard", "reactivation rate"
    elif "free" in t:
        audience, message, kpi = "Free users active in the last 14 days", "Automated AI weekly reports save about 6 hours a week", "conversion rate"
    elif "agency" in t:
        audience, message, kpi = "Customers in the Agency industry", "Manage every client in one place with white-labelled reports", "click-through rate"
    else:
        audience, message, kpi = "All opted-in customers", "Brightloop turns marketing data into plain-English reports", "click-through rate"
    offer = "Two months free on annual plans" if "annual" in t or "discount" in t or "offer" in t else ""
    return {"objective": f"Drive action from: {audience.lower()}", "channel": channel,
            "audience_description": audience, "key_message": message, "offer": offer, "kpi": kpi}


def _segment(user: str) -> dict:
    t = user.lower()
    brief = t.split("brief:", 1)[-1]
    pg = "dialect: postgresql" in t
    since = (lambda d: f"CAST(:today AS DATE) - INTERVAL '{d} days'") if pg else (lambda d: f"date(:today, '-{d} day')")
    conds = []
    if "free users" in brief:
        conds += ["plan = 'free'", f"last_active_date >= {since(14)}"]
    elif "inactive" in brief:
        conds += [f"last_active_date < {since(60)}"]
    elif "agency" in brief:
        conds += ["industry = 'Agency'"]
    for country in ("india", "uae"):
        if country in brief:
            conds.append(f"country = '{'India' if country == 'india' else 'UAE'}'")
    if '"channel": "email"' in brief:
        conds.append("email_opt_in = 1")
    where = " AND ".join(conds) or "1 = 1"
    return {"sql": f"SELECT * FROM customers WHERE {where}",
            "rationale": "Matches the audience described in the brief using plan, activity and consent fields."}


EMAIL_BODY = (
    "Hi {{first_name}},\n\n"
    "Every Monday your team spends hours pulling numbers from Google Ads, Meta and your CRM into one report. "
    "Brightloop Pro writes that report for you: a plain-English summary of what changed in your marketing "
    "and why, delivered automatically each week. Teams on Pro save about 6 hours a week on reporting, "
    "and you can connect up to 10 data sources in a few clicks.\n\n"
    "Try it on your own data and see this week's report in minutes.\n\n"
    "— The Brightloop team\n\nYou're receiving this because you opted in to Brightloop updates. Unsubscribe anytime."
)


def _content(user: str) -> dict:
    channel = re.search(r'"channel": "(email|social|ads)"', user)
    channel = channel.group(1) if channel else "email"
    first_draft = "Issues to fix" not in user and "Feedback from the marketing manager" not in user
    if channel == "ads":
        variants = [
            {"label": "Outcome-led", "subject_or_headline": "Reports that write themselves",
             "preview_text": "Brightloop turns your ad and email data into weekly plain-English reports.", "body": "", "cta": "Start free trial"},
            {"label": "Time-saving", "subject_or_headline": "Save 6 hours a week",
             "preview_text": "Connect Google Ads, Meta and HubSpot. Get AI reports every Monday.", "body": "", "cta": "See pricing"},
        ]
    elif channel == "social":
        variants = [
            {"label": "Case study", "subject_or_headline": "How agencies cut reporting time",
             "body": "Agencies on Brightloop manage every client in its own workspace and send white-labelled AI reports each week. The result: about 6 hours a week back for strategy instead of spreadsheets.",
             "cta": "Book a demo", "hashtags": ["#MarketingAnalytics", "#Agencies"]},
            {"label": "Feature tour", "subject_or_headline": "One dashboard for every channel",
             "body": "See email, social and paid ads side by side, with attribution that shows which channel really drove the sale. Brightloop writes the weekly summary for you.",
             "cta": "See your dashboard", "hashtags": ["#MarTech"]},
        ]
    else:
        variants = [
            {"label": "Outcome-led", "subject_or_headline": "{{first_name}}, get 6 hours back every week",
             "preview_text": "Brightloop Pro writes your weekly marketing report for you.", "body": EMAIL_BODY, "cta": "Start free trial"},
            {"label": "Feature spotlight", "subject_or_headline": "Your AI weekly report is ready to switch on",
             "preview_text": "Connect your ad platforms and let Brightloop summarise what changed.",
             "body": EMAIL_BODY.replace("Try it on your own data", "Upgrade in two minutes and get your first report"), "cta": "Upgrade to Pro"},
        ]
    if first_draft:
        variants[0]["subject_or_headline"] = variants[0]["subject_or_headline"].replace("get", "a game-changer:", 1) if channel == "email" else variants[0]["subject_or_headline"]
        variants[0]["preview_text"] = "A game-changer for your reporting. " + variants[0].get("preview_text", "")[:50]
    if "Feedback from the marketing manager" in user:
        variants[1]["label"] += " (revised)"
    return {"variants": variants, "send_time_recommendation": "Tuesday 9-11 am local time, when past emails had the best opens."}


def _review(user: str) -> dict:
    if "game-changer" in user.lower():
        return {"score": 5, "issues": ["Hype language does not match the practical brand voice."],
                "suggestions": ["Lead with the 6-hours-a-week outcome."]}
    return {"score": 8, "issues": [], "suggestions": ["Test the outcome-led subject against the feature spotlight."]}


def respond(schema_name: str, system: str, user: str) -> dict:
    handlers = {"CampaignBrief": _brief, "SegmentQuery": _segment, "CampaignContent": _content, "CampaignReview": _review}
    if schema_name not in handlers:
        raise ValueError(f"No fake response for {schema_name}")
    return handlers[schema_name](user)
