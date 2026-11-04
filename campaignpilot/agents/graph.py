"""The CampaignPilot multi-agent workflow, orchestrated with LangGraph.

    plan ─► segment ─► research ─► write ─► review ──(issues & revisions left)──► write
                                              │
                                              └─► human_approval ──approve──► finalize
                                                        │  └──reject──────────► finalize
                                                        └──revise (feedback)──► write

* Each node is one "agent" with a single responsibility and a typed output.
* Tools (database, RAG) are reached through MCP via the injected ToolClient.
* human_approval uses LangGraph's interrupt(): the run pauses, its state is
  saved by the checkpointer, and it resumes when a person approves, rejects
  or asks for changes — nothing is ever "sent" without human sign-off.
"""
from __future__ import annotations

import json
import logging
import operator
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from campaignpilot.agents import prompts
from campaignpilot.agents.guardrails import check_content, render
from campaignpilot.agents.schemas import CampaignBrief, CampaignContent, CampaignReview, SegmentQuery
from campaignpilot.config import get_settings
from campaignpilot.llm import LLMClient
from campaignpilot.tools.client import ToolClient, ToolError

log = logging.getLogger(__name__)


class CampaignState(TypedDict, total=False):
    goal: str
    channel: str  # optional hint from the user
    brief: dict
    segment: dict
    research: dict
    content: dict
    review: dict
    revision: int
    human_feedback: str
    decision: str  # approve | reject | revise
    status: str  # running | awaiting_approval | approved | rejected
    result: dict
    trace: Annotated[list[str], operator.add]  # append-only log of what each agent did


def _j(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)


def build_graph(llm: LLMClient, tools: ToolClient, checkpointer=None):
    settings = get_settings()

    # ------------------------------------------------------------ 1. planner
    def plan(state: CampaignState) -> dict:
        hint = f"\nThe marketer wants this channel: {state['channel']}" if state.get("channel") else ""
        brief = llm.generate_json(CampaignBrief, prompts.PLANNER, f"Goal: {state['goal']}{hint}")
        if state.get("channel"):
            brief.channel = state["channel"]  # explicit user choice always wins
        return {"brief": brief.model_dump(), "revision": 0, "status": "running",
                "trace": [f"planner: {brief.channel} campaign — {brief.objective}"]}

    # ------------------------------------------------------- 2. segmentation
    def segment(state: CampaignState) -> dict:
        brief = state["brief"]
        schema = tools.call("get_database_schema")
        feedback, trace = "", []
        for attempt in range(1, 4):  # self-correcting loop: errors are fed back to the LLM
            q = llm.generate_json(
                SegmentQuery, prompts.SEGMENTER,
                f"{schema}\n\nBrief:\n{_j(brief)}{feedback}",
            )
            try:
                result = tools.call("run_segment_query", sql=q.sql, sample_size=5)
            except ToolError as e:
                feedback = f"\n\nYour previous SQL failed:\n{q.sql}\nError: {e}\nFix it."
                trace.append(f"segmenter: attempt {attempt} failed ({e})")
                continue
            if brief["channel"] == "email" and "email_opt_in" not in q.sql.lower():
                feedback = f"\n\nYour previous SQL:\n{q.sql}\nis missing the required email_opt_in = 1 filter."
                trace.append(f"segmenter: attempt {attempt} rejected (no opt-in filter)")
                continue
            if result["count"] == 0:
                feedback = f"\n\nYour previous SQL:\n{q.sql}\nreturned 0 customers. Broaden it slightly."
                trace.append(f"segmenter: attempt {attempt} returned an empty segment")
                continue
            trace.append(f"segmenter: {result['count']} customers — {q.rationale}")
            return {"segment": {**result, "rationale": q.rationale}, "trace": trace}
        raise RuntimeError("Segmentation failed after 3 attempts: " + " | ".join(trace))

    # --------------------------------------------------------- 3. research
    def research(state: CampaignState) -> dict:
        b = state["brief"]
        queries = [
            f"brand guidelines voice banned words and {b['channel']} channel rules",
            f"{b['key_message']} {b['audience_description']}",
            f"past campaign learnings {b['channel']} {b['objective']}",
        ]
        seen, knowledge = set(), []
        for q in queries:
            for hit in tools.call("search_marketing_knowledge", query=q, k=3):
                key = (hit["source"], hit["heading"])
                if key not in seen:
                    seen.add(key)
                    knowledge.append(hit)
        perf = tools.call("get_campaign_performance", channel=b["channel"], top=3)
        bench = tools.call("get_channel_benchmarks")
        sources = sorted({f"{h['source']} › {h['heading']}" for h in knowledge})
        return {
            "research": {"knowledge": knowledge, "top_campaigns": perf, "benchmarks": bench},
            "trace": [f"researcher: {len(knowledge)} knowledge chunks, {len(perf)} past campaigns ({', '.join(sources[:4])}…)"],
        }

    # ---------------------------------------------------------- 4. writer
    def write(state: CampaignState) -> dict:
        b, seg, r = state["brief"], state["segment"], state["research"]
        context = "\n\n".join(h["text"] for h in r["knowledge"])
        to_fix = ""
        if state.get("review", {}).get("issues"):
            to_fix += "\n\nIssues to fix from the previous draft:\n- " + "\n- ".join(state["review"]["issues"])
            to_fix += f"\n\nPrevious draft:\n{_j(state.get('content'))}"
        if state.get("human_feedback"):
            to_fix += f"\n\nFeedback from the marketing manager (highest priority):\n{state['human_feedback']}"
        user = (
            f"Brief:\n{_j(b)}\n\nAudience: {seg['count']} customers. Why: {seg['rationale']}\n"
            f"Sample customers:\n{_j(seg['sample'][:3])}\n\n"
            f"Brand and product context:\n{context}\n\n"
            f"Best past {b['channel']} campaigns:\n{_j(r['top_campaigns'])}\n\n"
            f"Channel benchmarks:\n{_j(r['benchmarks'])}{to_fix}"
        )
        content = llm.generate_json(
            CampaignContent, prompts.CONTENT_WRITER.format(n_variants=2, channel=b["channel"]), user
        )
        revision = state.get("revision", 0) + (1 if state.get("content") else 0)
        return {"content": content.model_dump(), "revision": revision, "human_feedback": "",
                "trace": [f"writer: draft v{revision + 1} with {len(content.variants)} variants"]}

    # --------------------------------------------------------- 5. reviewer
    def review(state: CampaignState) -> dict:
        b, content = state["brief"], state["content"]
        rule_issues = check_content(b["channel"], content)
        context = "\n\n".join(h["text"] for h in state["research"]["knowledge"])
        rv = llm.generate_json(
            CampaignReview, prompts.REVIEWER,
            f"Brief:\n{_j(b)}\n\nContext:\n{context}\n\nDraft:\n{_j(content)}",
        )
        issues = rule_issues + rv.issues
        passed = not rule_issues and rv.score >= settings.review_pass_score
        result = {"score": rv.score, "issues": issues, "rule_issues": rule_issues,
                  "suggestions": rv.suggestions, "passed": passed}
        verdict = "passed" if passed else f"{len(issues)} issue(s)"
        return {"review": result, "trace": [f"reviewer: score {rv.score}/10, {verdict}"]}

    def after_review(state: CampaignState) -> Literal["write", "human_approval"]:
        if not state["review"]["passed"] and state.get("revision", 0) < settings.max_revisions:
            return "write"
        return "human_approval"

    # ---------------------------------------------------- 6. human approval
    def human_approval(state: CampaignState) -> dict:
        decision = interrupt({
            "message": "Review the campaign and approve, reject, or request changes.",
            "brief": state["brief"],
            "segment_size": state["segment"]["count"],
            "content": state["content"],
            "review": state["review"],
        })
        d = decision if isinstance(decision, dict) else {}
        action = d.get("decision", "reject")
        if action not in {"approve", "reject", "revise"}:
            action = "reject"  # fail safe: anything unclear never gets sent
        feedback = (d.get("feedback") or "").strip()
        return {"decision": action, "human_feedback": feedback,
                "trace": [f"human: {action}" + (f" — {feedback}" if feedback else "")]}

    def after_human(state: CampaignState) -> Literal["write", "finalize"]:
        return "write" if state["decision"] == "revise" else "finalize"

    # --------------------------------------------------------- 7. finalize
    def finalize(state: CampaignState) -> dict:
        approved = state["decision"] == "approve"
        result: dict = {"status": "approved" if approved else "rejected"}
        if approved:
            # Simulated send: personalised messages are written to the outbox,
            # which is where a real ESP / ads API integration would plug in.
            variants = state["content"]["variants"]
            previews = []
            for i, customer in enumerate(state["segment"]["sample"]):
                v = variants[i % len(variants)]  # simple A/B split
                previews.append({
                    "customer_id": customer.get("id"), "variant": v["label"],
                    "subject": render(v["subject_or_headline"], customer),
                    "body": render(v["body"], customer), "cta": v["cta"],
                })
            outbox = Path(settings.outbox_dir)
            outbox.mkdir(parents=True, exist_ok=True)
            fname = outbox / f"campaign_{datetime.now():%Y%m%d_%H%M%S}.json"
            fname.write_text(_j({"brief": state["brief"], "segment_sql": state["segment"]["sql"],
                                 "audience_size": state["segment"]["count"], "messages": previews}))
            result.update({"previews": previews, "outbox_file": str(fname)})
        return {"status": result["status"], "result": result, "trace": [f"finalize: {result['status']}"]}

    g = StateGraph(CampaignState)
    for name, fn in [("plan", plan), ("segment", segment), ("research", research), ("write", write),
                     ("review", review), ("human_approval", human_approval), ("finalize", finalize)]:
        g.add_node(name, fn)
    g.add_edge(START, "plan")
    g.add_edge("plan", "segment")
    g.add_edge("segment", "research")
    g.add_edge("research", "write")
    g.add_edge("write", "review")
    g.add_conditional_edges("review", after_review, ["write", "human_approval"])
    g.add_conditional_edges("human_approval", after_human, ["write", "finalize"])
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer)
