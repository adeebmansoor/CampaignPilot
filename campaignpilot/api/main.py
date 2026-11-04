"""REST API for CampaignPilot.

POST /campaigns                    start a run (returns when it pauses for approval)
GET  /campaigns/{thread_id}        current state of a run
POST /campaigns/{thread_id}/decision   approve | reject | revise (with feedback)
GET  /campaigns                    approved / rejected campaigns history
GET  /analytics/benchmarks         channel benchmarks from the warehouse
GET  /analytics/campaigns          past campaign performance
GET  /health                       liveness + which LLM / tools mode is active
"""
import logging
import time
import uuid
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from campaignpilot.agents import service
from campaignpilot.config import get_settings
from campaignpilot.tools import marketing_tools

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")

app = FastAPI(title="CampaignPilot", version="1.0.0",
              description="Multi-agent marketing campaign assistant (LangGraph + RAG + MCP)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def request_log(request: Request, call_next):
    rid = uuid.uuid4().hex[:8]
    started = time.perf_counter()
    response = await call_next(request)
    log.info("%s %s %s -> %d in %.0fms", rid, request.method, request.url.path,
             response.status_code, (time.perf_counter() - started) * 1000)
    response.headers["X-Request-ID"] = rid
    return response


class StartRequest(BaseModel):
    goal: str = Field(min_length=10, max_length=1000,
                      examples=["Convert active free users to Pro with an email about AI weekly reports"])
    channel: Literal["email", "social", "ads"] | None = None


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject", "revise"]
    feedback: str = Field(default="", max_length=2000)


@app.get("/health")
def health() -> dict:
    s = get_settings()
    return {"status": "ok", "llm_provider": s.llm_provider, "tools_mode": s.tools_mode}


@app.post("/campaigns", status_code=201)
def start_campaign(req: StartRequest) -> dict:
    try:
        return service.start_run(req.goal, req.channel)
    except RuntimeError as e:  # e.g. segmentation failed after retries, LLM failure
        log.exception("run failed")
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/campaigns")
def list_campaigns(limit: int = 20) -> list[dict]:
    return service.list_campaigns(min(max(limit, 1), 100))


@app.get("/campaigns/{thread_id}")
def get_campaign(thread_id: str) -> dict:
    try:
        return service.get_run(thread_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="run not found") from None


@app.post("/campaigns/{thread_id}/decision")
def decide(thread_id: str, req: DecisionRequest) -> dict:
    if req.decision == "revise" and not req.feedback.strip():
        raise HTTPException(status_code=422, detail="feedback is required when requesting a revision")
    try:
        return service.resume_run(thread_id, req.decision, req.feedback)
    except KeyError:
        raise HTTPException(status_code=404, detail="run not found") from None
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@app.get("/analytics/benchmarks")
def benchmarks() -> list[dict]:
    return marketing_tools.get_channel_benchmarks()


@app.get("/analytics/campaigns")
def campaign_performance(channel: Literal["email", "social", "ads"] | None = None, top: int = 20) -> list[dict]:
    return marketing_tools.get_campaign_performance(channel, top)
