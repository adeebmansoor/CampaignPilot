from fastapi.testclient import TestClient

from campaignpilot.api.main import app

client = TestClient(app)
GOAL = "Win back customers inactive for more than 60 days with an email"


def test_health():
    assert client.get("/health").json()["llm_provider"] == "fake"


def test_full_campaign_flow():
    r = client.post("/campaigns", json={"goal": GOAL})
    assert r.status_code == 201
    run = r.json()
    assert run["status"] == "awaiting_approval"
    assert run["approval_request"]["segment_size"] > 0
    tid = run["thread_id"]

    assert client.get(f"/campaigns/{tid}").json()["status"] == "awaiting_approval"
    assert client.post(f"/campaigns/{tid}/decision", json={"decision": "revise"}).status_code == 422

    done = client.post(f"/campaigns/{tid}/decision", json={"decision": "approve"}).json()
    assert done["status"] == "approved"
    # deciding twice is a conflict
    assert client.post(f"/campaigns/{tid}/decision", json={"decision": "approve"}).status_code == 409

    history = client.get("/campaigns").json()
    assert any(h["thread_id"] == tid and h["status"] == "approved" for h in history)


def test_unknown_run_404():
    assert client.get("/campaigns/doesnotexist").status_code == 404


def test_validation():
    assert client.post("/campaigns", json={"goal": "short"}).status_code == 422
    assert client.post("/campaigns", json={"goal": GOAL, "channel": "tv"}).status_code == 422


def test_analytics():
    bench = client.get("/analytics/benchmarks").json()
    assert {b["channel"] for b in bench} == {"email", "social", "ads"}
    assert len(client.get("/analytics/campaigns?channel=email").json()) > 0
