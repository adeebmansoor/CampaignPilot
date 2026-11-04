from campaignpilot.agents.guardrails import check_content, check_variant, render

GOOD_BODY = ("Hi {{first_name}}, " + "Brightloop writes your weekly marketing report for you. " * 8 +
             "Unsubscribe anytime.")


def email(**overrides):
    v = {"label": "A", "subject_or_headline": "{{first_name}}, save 6 hours a week",
         "preview_text": "Automated AI reports.", "body": GOOD_BODY, "cta": "Start free trial"}
    v.update(overrides)
    return v


def test_clean_email_passes():
    assert check_variant("email", email()) == []


def test_banned_phrase_and_exclamations():
    issues = check_variant("email", email(subject_or_headline="A revolutionary report!!"))
    assert any("revolutionary" in i for i in issues)
    assert any("exclamation" in i for i in issues)


def test_email_requires_unsubscribe_and_length():
    issues = check_variant("email", email(body="Short body with no footer."))
    assert any("unsubscribe" in i for i in issues)
    assert any("min 60" in i for i in issues)


def test_unknown_token_and_caps():
    issues = check_variant("email", email(subject_or_headline="HELLO {{company}} TEAM NOW"))
    assert any("company" in i for i in issues)
    assert any("ALL CAPS" in i for i in issues)


def test_ads_rules():
    issues = check_variant("ads", {"label": "Ad", "subject_or_headline": "x" * 35,
                                   "preview_text": "ok", "body": "", "cta": "Buy now"})
    assert any("max 30" in i for i in issues)
    assert any("CTA must be one of" in i for i in issues)


def test_no_variants():
    assert check_content("email", {"variants": []}) == ["no content variants were produced"]


def test_render():
    assert render("Hi {{ first_name }}", {"first_name": "Noor"}) == "Hi Noor"
    assert render("Hi {{first_name}}", {}) == "Hi there"
