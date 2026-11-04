"""Deterministic brand and compliance checks.

The LLM reviewer judges quality; these rules are checked in code because
they must never depend on a model's mood: banned words, length limits,
unsubscribe lines, allowed personalisation tokens. Rules mirror
data/knowledge/brand_guidelines.md.
"""
import re

BANNED_PHRASES = [
    "revolutionary", "game-changer", "game changer", "guaranteed", "best in the world",
    "100% free", "act now", "limited time only", "click here",
]
ALLOWED_TOKENS = {"first_name"}
AD_CTAS = {"start free trial", "book a demo", "see pricing", "learn more"}

LIMITS = {
    "email": {"subject": 60, "preview": 90, "body_min_words": 60, "body_max_words": 180},
    "social": {"body_max_words": 220, "hashtags": 3},
    "ads": {"subject": 30, "preview": 90},
}


def _words(text: str) -> int:
    return len(re.findall(r"\b\w[\w'-]*\b", text))


def check_variant(channel: str, v: dict) -> list[str]:
    label = v.get("label") or "variant"
    issues: list[str] = []
    subject, preview, body, cta = v.get("subject_or_headline", ""), v.get("preview_text", ""), v.get("body", ""), v.get("cta", "")
    full = " ".join([subject, preview, body, cta]).lower()

    for phrase in BANNED_PHRASES:
        if phrase in full:
            issues.append(f"[{label}] uses banned phrase '{phrase}'")
    if full.count("!") > 1:
        issues.append(f"[{label}] has more than one exclamation mark")
    for token in set(re.findall(r"\{\{\s*(\w+)\s*\}\}", full)):
        if token not in ALLOWED_TOKENS:
            issues.append(f"[{label}] uses unknown personalisation token '{{{{{token}}}}}' (only {{{{first_name}}}} is allowed)")
    letters = re.sub(r"[^A-Za-z]", "", re.sub(r"\{\{.*?\}\}", "", subject))  # ignore tokens
    if len(letters) > 8 and letters.isupper():
        issues.append(f"[{label}] subject/headline is in ALL CAPS")
    if not cta.strip():
        issues.append(f"[{label}] is missing a call to action")

    lim = LIMITS.get(channel, {})
    if "subject" in lim and len(subject) > lim["subject"]:
        issues.append(f"[{label}] subject/headline is {len(subject)} chars (max {lim['subject']})")
    if "preview" in lim and len(preview) > lim["preview"]:
        issues.append(f"[{label}] preview/description is {len(preview)} chars (max {lim['preview']})")
    n = _words(body)
    if "body_min_words" in lim and n < lim["body_min_words"]:
        issues.append(f"[{label}] body is {n} words (min {lim['body_min_words']})")
    if "body_max_words" in lim and n > lim["body_max_words"]:
        issues.append(f"[{label}] body is {n} words (max {lim['body_max_words']})")
    if "hashtags" in lim and len(v.get("hashtags") or []) > lim["hashtags"]:
        issues.append(f"[{label}] has more than {lim['hashtags']} hashtags")

    if channel == "email" and "unsubscribe" not in body.lower():
        issues.append(f"[{label}] email body must include an unsubscribe line")
    if channel == "ads" and cta.strip().lower() not in AD_CTAS:
        issues.append(f"[{label}] ad CTA must be one of: {', '.join(sorted(AD_CTAS))}")
    return issues


def check_content(channel: str, content: dict) -> list[str]:
    variants = content.get("variants") or []
    if not variants:
        return ["no content variants were produced"]
    return [issue for v in variants for issue in check_variant(channel, v)]


def render(text: str, customer: dict) -> str:
    """Fill personalisation tokens for one customer (used for previews / sending)."""
    return re.sub(r"\{\{\s*first_name\s*\}\}", customer.get("first_name") or "there", text)
