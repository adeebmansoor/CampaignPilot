"""Run, pause and resume campaign workflows. Shared by the API and the CLI.

Every run has a thread_id. The checkpointer (SQLite) stores the graph state
after each step, so a run waiting for human approval survives an API restart.
"""
from __future__ import annotations

import sqlite3
import uuid
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


@lru_cache
def get_checkpointer() -> SqliteSaver:
    path = Path(get_settings().checkpoint_db)
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False))


@lru_cache
def get_graph():
    init_db()
    return build_graph(get_llm(), get_tool_client(), get_checkpointer())


def _snapshot(thread_id: str) -> dict:
    graph = get_graph()
    snap = graph.get_state({"configurable": {"thread_id": thread_id}})
    if not snap.values:
        raise KeyError(thread_id)
    values = dict(snap.values)
    pending = [i.value for t in snap.tasks for i in (t.interrupts or [])]
    status = "awaiting_approval" if pending else values.get("status", "running")
    return {"thread_id": thread_id, "status": status, "next": list(snap.next), "state": values,
            "approval_request": pending[0] if pending else None}


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


def start_run(goal: str, channel: str | None = None) -> dict:
    thread_id = uuid.uuid4().hex
    inputs = {"goal": goal, "trace": []}
    if channel:
        inputs["channel"] = channel
    get_graph().invoke(inputs, {"configurable": {"thread_id": thread_id}})
    return _snapshot(thread_id)


def resume_run(thread_id: str, decision: str, feedback: str = "") -> dict:
    view = _snapshot(thread_id)
    if view["status"] != "awaiting_approval":
        raise ValueError(f"run {thread_id} is not waiting for approval (status: {view['status']})")
    get_graph().invoke(Command(resume={"decision": decision, "feedback": feedback}),
                       {"configurable": {"thread_id": thread_id}})
    view = _snapshot(thread_id)
    _persist(thread_id, view)
    return view


def get_run(thread_id: str) -> dict:
    return _snapshot(thread_id)


def list_campaigns(limit: int = 20) -> list[dict]:
    init_db()
    with get_session() as db:
        rows = db.query(GeneratedCampaign).order_by(GeneratedCampaign.id.desc()).limit(limit).all()
        return [{"id": r.id, "thread_id": r.thread_id, "goal": r.goal, "channel": r.channel,
                 "status": r.status, "segment_size": r.segment_size, "revisions": r.revisions,
                 "score": (r.review or {}).get("score"), "created_at": str(r.created_at)} for r in rows]
