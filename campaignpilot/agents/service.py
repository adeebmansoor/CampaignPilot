"""Run, pause and resume campaign workflows. Shared by the API and the CLI.

Every run has a thread_id. The checkpointer (SQLite) stores the graph state
after each agent finishes, so:
* the web UI can poll a run and show agent-by-agent progress while it works;
* a run waiting for human approval survives an API restart.

Runs can be executed synchronously (CLI, tests) or in a background worker
thread (web UI), in which case the caller polls get_run().
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from campaignpilot.agents.graph import build_graph
from campaignpilot.config import get_settings
from campaignpilot.db.models import GeneratedCampaign
from campaignpilot.db.session import get_session, init_db
from campaignpilot.llm import get_llm
from campaignpilot.tools.client import get_tool_client

log = logging.getLogger(__name__)

# In-memory registry of runs started by this process: which ones are executing
# right now, and the error message of any that failed.
_RUNS: dict[str, dict] = {}
_RUNS_LOCK = threading.Lock()
_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="campaign-run")


@lru_cache
def get_checkpointer() -> SqliteSaver:
    path = Path(get_settings().checkpoint_db)
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False))


@lru_cache
def get_graph():
    init_db()
    return build_graph(get_llm(), get_tool_client(), get_checkpointer())


def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def _mark(thread_id: str, **fields) -> None:
    with _RUNS_LOCK:
        _RUNS.setdefault(thread_id, {}).update(fields)


def _snapshot(thread_id: str) -> dict:
    meta = _RUNS.get(thread_id, {})
    snap = get_graph().get_state(_cfg(thread_id))
    if not snap.values:
        if not meta:
            raise KeyError(thread_id)
        values = {"goal": meta.get("goal", ""), "trace": []}  # started, no agent finished yet
        nxt, pending = ["plan"], []
    else:
        values = dict(snap.values)
        nxt = ["plan" if n == "__start__" else n for n in snap.next]  # input saved, planner about to run
        pending = [i.value for t in snap.tasks for i in (t.interrupts or [])]

    if meta.get("error"):
        status = "error"
    elif meta.get("active"):
        status = "running"
    elif pending:
        status = "awaiting_approval"
    else:
        status = values.get("status", "running")
    return {
        "thread_id": thread_id,
        "status": status,
        "next": nxt if status == "running" else [],
        "error": meta.get("error"),
        "state": values,
        "approval_request": pending[0] if pending and status == "awaiting_approval" else None,
    }


def _persist(thread_id: str, view: dict) -> None:
    if view["status"] not in {"approved", "rejected"}:
        return
    s = view["state"]
    with get_session() as db:
        row = db.query(GeneratedCampaign).filter_by(thread_id=thread_id).one_or_none() or GeneratedCampaign(thread_id=thread_id)
        row.goal, row.channel, row.status = s["goal"], s["brief"]["channel"], view["status"]
        row.segment_sql, row.segment_size = s["segment"]["sql"], s["segment"]["count"]
        row.content, row.review, row.revisions = s.get("content", {}), s.get("review", {}), s.get("revision", 0)
        db.add(row)


def _execute(thread_id: str, payload) -> dict:
    """Run the graph until it pauses or finishes. Errors are recorded, not lost."""
    _mark(thread_id, active=True, error=None)
    started = time.perf_counter()
    try:
        get_graph().invoke(payload, _cfg(thread_id))
    except Exception as e:  # noqa: BLE001 - surfaced to the UI via status "error"
        log.exception("run %s failed", thread_id)
        _mark(thread_id, active=False, error=f"{type(e).__name__}: {e}")
        return _snapshot(thread_id)
    _mark(thread_id, active=False)
    view = _snapshot(thread_id)
    _persist(thread_id, view)
    log.info("run %s -> %s in %.1fs", thread_id, view["status"], time.perf_counter() - started)
    return view


def _inputs(goal: str, channel: str | None) -> dict:
    inputs = {"goal": goal, "trace": []}
    if channel:
        inputs["channel"] = channel
    return inputs


def _check_resumable(thread_id: str) -> None:
    view = _snapshot(thread_id)
    if view["status"] != "awaiting_approval":
        raise ValueError(f"run {thread_id} is not waiting for approval (status: {view['status']})")


# ----------------------------------------------------------------- public API
def start_run(goal: str, channel: str | None = None) -> dict:
    """Synchronous: returns when the run pauses for approval (or fails)."""
    thread_id = uuid.uuid4().hex
    _mark(thread_id, goal=goal)
    view = _execute(thread_id, _inputs(goal, channel))
    if view["status"] == "error":
        raise RuntimeError(view["error"])
    return view


def start_run_background(goal: str, channel: str | None = None) -> dict:
    """Returns immediately; poll get_run() to watch the agents work."""
    thread_id = uuid.uuid4().hex
    _mark(thread_id, goal=goal, active=True)
    _EXECUTOR.submit(_execute, thread_id, _inputs(goal, channel))
    return _snapshot(thread_id)


def resume_run(thread_id: str, decision: str, feedback: str = "") -> dict:
    _check_resumable(thread_id)
    view = _execute(thread_id, Command(resume={"decision": decision, "feedback": feedback}))
    if view["status"] == "error":
        raise RuntimeError(view["error"])
    return view


def resume_run_background(thread_id: str, decision: str, feedback: str = "") -> dict:
    _check_resumable(thread_id)
    _mark(thread_id, active=True, error=None)
    _EXECUTOR.submit(_execute, thread_id, Command(resume={"decision": decision, "feedback": feedback}))
    return _snapshot(thread_id)


def get_run(thread_id: str) -> dict:
    return _snapshot(thread_id)


def list_campaigns(limit: int = 20) -> list[dict]:
    init_db()
    with get_session() as db:
        rows = db.query(GeneratedCampaign).order_by(GeneratedCampaign.id.desc()).limit(limit).all()
        return [{"id": r.id, "thread_id": r.thread_id, "goal": r.goal, "channel": r.channel,
                 "status": r.status, "segment_size": r.segment_size, "revisions": r.revisions,
                 "score": (r.review or {}).get("score"), "created_at": str(r.created_at)} for r in rows]
