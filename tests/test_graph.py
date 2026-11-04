from pathlib import Path

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from campaignpilot.agents.graph import build_graph
from campaignpilot.agents.schemas import SegmentQuery
from campaignpilot.llm import FakeLLMClient
from campaignpilot.tools.client import DirectToolClient

GOAL = "Convert active free users to Pro with an email about AI weekly reports"


@pytest.fixture
def graph():
    return build_graph(FakeLLMClient(), DirectToolClient(), MemorySaver())


def cfg(tid):
    return {"configurable": {"thread_id": tid}}


def pending(graph, tid):
    return [i.value for t in graph.get_state(cfg(tid)).tasks for i in t.interrupts]


def test_run_pauses_for_human_after_revision_loop(graph):
    state = graph.invoke({"goal": GOAL, "trace": []}, cfg("t1"))
    assert pending(graph, "t1"), "graph should pause at human approval"
    assert state["brief"]["channel"] == "email"
    assert "email_opt_in = 1" in state["segment"]["sql"]
    assert state["segment"]["count"] > 0
    # the fake writer's first draft breaks a brand rule, so the reviewer must send it back once
    assert state["revision"] == 1
    assert state["review"]["passed"] and state["review"]["rule_issues"] == []
    assert any(h["source"] == "brand_guidelines.md" for h in state["research"]["knowledge"])


def test_approve_writes_personalised_outbox(graph):
    graph.invoke({"goal": GOAL, "trace": []}, cfg("t2"))
    state = graph.invoke(Command(resume={"decision": "approve"}), cfg("t2"))
    assert state["status"] == "approved"
    previews = state["result"]["previews"]
    assert previews and "{{first_name}}" not in previews[0]["subject"] + previews[0]["body"]
    assert Path(state["result"]["outbox_file"]).exists()


def test_reject_sends_nothing(graph):
    graph.invoke({"goal": GOAL, "trace": []}, cfg("t3"))
    state = graph.invoke(Command(resume={"decision": "reject"}), cfg("t3"))
    assert state["status"] == "rejected" and "outbox_file" not in state["result"]


def test_revise_with_feedback_loops_back(graph):
    graph.invoke({"goal": GOAL, "trace": []}, cfg("t4"))
    state = graph.invoke(Command(resume={"decision": "revise", "feedback": "Mention the annual plan"}), cfg("t4"))
    assert pending(graph, "t4"), "should pause for approval again after the rewrite"
    assert any("(revised)" in v["label"] for v in state["content"]["variants"])


def test_unknown_decision_fails_safe(graph):
    graph.invoke({"goal": GOAL, "trace": []}, cfg("t5"))
    state = graph.invoke(Command(resume={"decision": "yolo"}), cfg("t5"))
    assert state["status"] == "rejected"


def test_explicit_channel_wins(graph):
    state = graph.invoke({"goal": "Promote agency workspaces to agencies on LinkedIn", "channel": "social", "trace": []}, cfg("t6"))
    assert state["brief"]["channel"] == "social"
    assert "email_opt_in" not in state["segment"]["sql"]


class SelfCorrectingLLM(FakeLLMClient):
    """First SQL attempt is broken; the segmenter must feed the error back and retry."""

    def __init__(self):
        self.sql_calls = 0

    def generate_json(self, schema, system, user):
        if schema is SegmentQuery:
            self.sql_calls += 1
            if self.sql_calls == 1:
                return SegmentQuery(sql="SELECT * FROM customers WHERE plann = 'free'", rationale="typo")
            assert "Error:" in user  # the DB error was given back to the model
        return super().generate_json(schema, system, user)


def test_segmenter_self_corrects_bad_sql():
    llm = SelfCorrectingLLM()
    g = build_graph(llm, DirectToolClient(), MemorySaver())
    state = g.invoke({"goal": GOAL, "trace": []}, cfg("t7"))
    assert llm.sql_calls == 2
    assert any("attempt 1 failed" in line for line in state["trace"])
