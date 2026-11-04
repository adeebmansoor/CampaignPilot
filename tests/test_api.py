import time

import pytest
from fastapi.testclient import TestClient

from campaignpilot.api import main
from campaignpilot.config import get_settings

client = TestClient(main.app)
GOAL = "Win back customers inactive for more than 60 days with an email"


def test_health_and_web_app():
    assert client.get("/health").json()["llm_provider"] == "fake"
    page = client.get("/")
    assert page.status_code == 200 and "CampaignPilot" in page.text
    assert client.get("/static/app.js").status_code == 200


def test_full_campaign_flow_sync():
    r = client.post("/campaigns?wait=true", json={"goal": GOAL})
    assert r.status_code == 201
    run = r.json()
    assert run["status"] == "awaiting_approval"
    assert run["approval_request"]["segment_size"] > 0
    tid = run["thread_id"]

    assert client.get(f"/campaigns/{tid}").json()["status"] == "awaiting_approval"
    assert client.post(f"/campaigns/{tid}/decision", json={"decision": "revise"}).status_code == 422

    done = client.post(f"/campaigns/{tid}/decision?wait=true", json={"decision": "approve"}).json()
    assert done["status"] == "approved"
    # deciding twice is a conflict
    assert client.post(f"/campaigns/{tid}/decision", json={"decision": "approve"}).status_code == 409

    history = client.get("/campaigns").json()
    assert any(h["thread_id"] == tid and h["status"] == "approved" for h in history)


def poll(tid, until, timeout=60):
    deadline = time.time() + timeout
    seen_running = False
    while time.time() < deadline:
        run = client.get(f"/campaigns/{tid}").json()
        seen_running |= run["status"] == "running"
        if run["status"] in until:
            return run, seen_running
        time.sleep(0.1)
    raise AssertionError(f"run {tid} never reached {until}")


def test_background_run_can_be_polled():
    run = client.post("/campaigns", json={"goal": GOAL}).json()
    assert run["status"] == "running" and run["next"] == ["plan"]
    run, _ = poll(run["thread_id"], {"awaiting_approval"})
    assert len(run["state"]["trace"]) >= 5
    resumed = client.post(f"/campaigns/{run['thread_id']}/decision", json={"decision": "reject"}).json()
    assert resumed["status"] == "running"
    final, _ = poll(run["thread_id"], {"rejected"})
    assert "outbox_file" not in final["state"]["result"]


def test_unknown_run_404():
    assert client.get("/campaigns/doesnotexist").status_code == 404


def test_validation():
    assert client.post("/campaigns", json={"goal": "short"}).status_code == 422
    assert client.post("/campaigns", json={"goal": GOAL, "channel": "tv"}).status_code == 422


def test_analytics():
    bench = client.get("/analytics/benchmarks").json()
    assert {b["channel"] for b in bench} == {"email", "social", "ads"}
    assert len(client.get("/analytics/campaigns?channel=email").json()) > 0


@pytest.fixture
def settings_override(monkeypatch):
    s = get_settings()

    def apply(**kw):
        for k, v in kw.items():
            monkeypatch.setattr(s, k, v)
    return apply


def test_access_code(settings_override):
    settings_override(access_code="letmein")
    assert client.get("/health").json()["access_code_required"] is True
    assert client.post("/campaigns", json={"goal": GOAL}).status_code == 401
    assert client.post("/campaigns", json={"goal": GOAL}, headers={"X-Access-Code": "nope"}).status_code == 401
    ok = client.post("/campaigns?wait=true", json={"goal": GOAL}, headers={"X-Access-Code": "letmein"})
    assert ok.status_code == 201


def test_rate_limit(settings_override, monkeypatch):
    monkeypatch.setattr(main, "run_limiter", main.RateLimiter())
    settings_override(max_runs_per_hour=1)
    assert client.post("/campaigns?wait=true", json={"goal": GOAL}).status_code == 201
    r = client.post("/campaigns?wait=true", json={"goal": GOAL})
    assert r.status_code == 429 and "Retry-After" in r.headers
