"""REST API + web app for CampaignPilot.

GET  /                              the web app (campaignpilot/web)
POST /campaigns                     start a run (?wait=true blocks until it pauses)
GET  /campaigns/{thread_id}         current state; poll this to watch agents work
POST /campaigns/{thread_id}/decision   approve | reject | revise (with feedback)
GET  /campaigns                     finished campaigns
GET  /analytics/benchmarks          channel benchmarks from the warehouse
GET  /analytics/campaigns           past campaign performance
GET  /health                        liveness and configuration
"""
import hmac
import logging
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.exc import OperationalError, ProgrammingError

from campaignpilot.agents import service
from campaignpilot.config import get_settings
from campaignpilot.tools import marketing_tools

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("api")

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="CampaignPilot", version="1.1.0",
              description="Multi-agent marketing campaign assistant (LangGraph + RAG + MCP)")
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.middleware("http")
async def request_log(request: Request, call_next):
    rid = uuid.uuid4().hex[:8]
    started = time.perf_counter()
    response = await call_next(request)
    if request.url.path != "/health" and not request.url.path.startswith("/static"):
        log.info("%s %s %s -> %d in %.0fms", rid, request.method, request.url.path,
                 response.status_code, (time.perf_counter() - started) * 1000)
    response.headers["X-Request-ID"] = rid
    return response


# ------------------------------------------------------------ demo protection
def require_access_code(x_access_code: str = Header(default="")) -> None:
    expected = get_settings().access_code
    if expected and not hmac.compare_digest(x_access_code.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Enter the access code to run campaigns.")


class RateLimiter:
    """Global sliding-window limit on new runs (protects the free LLM quota)."""

    def __init__(self) -> None:
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def check(self, limit: int, window: float = 3600) -> None:
        now = time.monotonic()
        with self._lock:
            while self._hits and now - self._hits[0] > window:
                self._hits.popleft()
            if limit and len(self._hits) >= limit:
                retry = int(window - (now - self._hits[0])) + 1
                raise HTTPException(status_code=429, detail=f"The demo hit its hourly limit. Try again in {retry // 60 + 1} minutes.",
                                    headers={"Retry-After": str(retry)})
            self._hits.append(now)


run_limiter = RateLimiter()


# ------------------------------------------------------------------- schemas
class StartRequest(BaseModel):
    goal: str = Field(min_length=10, max_length=1000,
                      examples=["Convert active free users to Pro with an email about AI weekly reports"])
    channel: Literal["email", "social", "ads"] | None = None


class DecisionRequest(BaseModel):
    decision: Literal["approve", "reject", "revise"]
    feedback: str = Field(default="", max_length=2000)


# -------------------------------------------------------------------- routes
@app.get("/", include_in_schema=False)
def web_app() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    s = get_settings()
    return {"status": "ok", "llm_provider": s.llm_provider, "model": {"gemini": s.gemini_model, "gateway": f"LLM Gateway, route {s.gateway_route}"}.get(s.llm_provider, "fake"),
            "tools_mode": s.tools_mode, "access_code_required": bool(s.access_code)}


@app.post("/campaigns", status_code=201, dependencies=[Depends(require_access_code)])
def start_campaign(req: StartRequest, wait: bool = False) -> dict:
    run_limiter.check(get_settings().max_runs_per_hour)
    if not wait:
        return service.start_run_background(req.goal, req.channel)
    try:
        return service.start_run(req.goal, req.channel)
    except RuntimeError as e:  # e.g. segmentation failed after retries, LLM failure
        raise HTTPException(status_code=502, detail=str(e)) from e


@app.get("/campaigns")
def list_campaigns(limit: int = 20) -> list[dict]:
    return service.list_campaigns(min(max(limit, 1), 100))


@app.get("/campaigns/{thread_id}")
def get_campaign(thread_id: str) -> dict:
    try:
        return service.get_run(thread_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found. It may have been cleared when the app restarted.") from None


@app.post("/campaigns/{thread_id}/decision", dependencies=[Depends(require_access_code)])
def decide(thread_id: str, req: DecisionRequest, wait: bool = False) -> dict:
    if req.decision == "revise" and not req.feedback.strip():
        raise HTTPException(status_code=422, detail="Add feedback so the writer knows what to change.")
    try:
        if wait:
            return service.resume_run(thread_id, req.decision, req.feedback)
        return service.resume_run_background(thread_id, req.decision, req.feedback)
    except KeyError:
        raise HTTPException(status_code=404, detail="Run not found.") from None
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e


def _warehouse(fn, *args):
    try:
        return fn(*args)
    except (OperationalError, ProgrammingError):  # tables not created yet: pipeline still loading
        raise HTTPException(status_code=503, detail="Data is still loading. Refresh in a few seconds.") from None


@app.get("/analytics/benchmarks")
def benchmarks() -> list[dict]:
    return _warehouse(marketing_tools.get_channel_benchmarks)


@app.get("/analytics/campaigns")
def campaign_performance(channel: Literal["email", "social", "ads"] | None = None, top: int = 20) -> list[dict]:
    return _warehouse(marketing_tools.get_campaign_performance, channel, top)
