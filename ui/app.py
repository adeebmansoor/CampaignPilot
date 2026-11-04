"""Streamlit front end. Talks only to the REST API (API_URL), like a real client.

    streamlit run ui/app.py
"""
import os

import pandas as pd
import requests
import streamlit as st

API = os.getenv("API_URL", "http://localhost:8000")

st.set_page_config(page_title="CampaignPilot", page_icon="📣", layout="wide")


def api(method: str, path: str, **kwargs):
    try:
        r = requests.request(method, f"{API}{path}", timeout=300, **kwargs)
    except requests.RequestException as e:
        st.error(f"Cannot reach the API at {API}: {e}")
        st.stop()
    if r.status_code >= 400:
        st.error(f"API error {r.status_code}: {r.json().get('detail', r.text)}")
        return None
    return r.json()


st.title("📣 CampaignPilot")
health = api("GET", "/health") or {}
st.caption(f"Multi-agent campaign assistant · LLM: **{health.get('llm_provider')}** · tools via **{health.get('tools_mode')}**")

tab_new, tab_history, tab_analytics = st.tabs(["New campaign", "History", "Analytics"])

with tab_new:
    with st.form("start"):
        goal = st.text_area(
            "Campaign goal",
            "Convert free users who were active in the last two weeks to Pro, focusing on automated AI weekly reports.",
            height=90,
        )
        channel = st.selectbox("Channel", ["(let the planner decide)", "email", "social", "ads"])
        submitted = st.form_submit_button("Run agents", type="primary")
    if submitted:
        with st.spinner("Planner → Segmenter → Researcher → Writer → Reviewer…"):
            run = api("POST", "/campaigns", json={"goal": goal, "channel": None if channel.startswith("(") else channel})
        if run:
            st.session_state["run"] = run

    run = st.session_state.get("run")
    if run:
        state = run["state"]
        st.subheader(f"Status: {run['status'].replace('_', ' ')}")
        with st.expander("Agent trace", expanded=True):
            for line in state.get("trace", []):
                st.markdown(f"- {line}")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("#### Brief")
            st.json(state.get("brief", {}), expanded=False)
            seg = state.get("segment", {})
            st.markdown(f"#### Audience: {seg.get('count', 0):,} customers")
            st.code(seg.get("sql", ""), language="sql")
            if seg.get("sample"):
                st.dataframe(pd.DataFrame(seg["sample"]), hide_index=True)
        with c2:
            review = state.get("review", {})
            st.markdown(f"#### Review score: {review.get('score', '–')}/10")
            for issue in review.get("issues", []):
                st.warning(issue)
            for s in review.get("suggestions", []):
                st.info(s)
            with st.expander("Retrieved knowledge (RAG sources)"):
                for h in state.get("research", {}).get("knowledge", []):
                    st.markdown(f"**{h['source']} › {h['heading']}** (score {h['score']})")

        st.markdown("#### Content variants")
        cols = st.columns(len(state.get("content", {}).get("variants", [])) or 1)
        for col, v in zip(cols, state.get("content", {}).get("variants", []), strict=False):
            with col, st.container(border=True):
                st.markdown(f"**{v['label']}**")
                st.markdown(f"**{v['subject_or_headline']}**")
                if v.get("preview_text"):
                    st.caption(v["preview_text"])
                st.text(v["body"])
                st.button(v["cta"], disabled=True, key=f"cta-{v['label']}")
                if v.get("hashtags"):
                    st.caption(" ".join(v["hashtags"]))

        if run["status"] == "awaiting_approval":
            st.markdown("#### Your decision")
            feedback = st.text_input("Feedback (required for revisions)")
            a, b, c = st.columns(3)
            decision = None
            if a.button("✅ Approve & send", type="primary"):
                decision = "approve"
            if b.button("✏️ Request changes"):
                decision = "revise"
            if c.button("❌ Reject"):
                decision = "reject"
            if decision:
                with st.spinner("Resuming workflow…"):
                    new = api("POST", f"/campaigns/{run['thread_id']}/decision", json={"decision": decision, "feedback": feedback})
                if new:
                    st.session_state["run"] = new
                    st.rerun()
        elif run["status"] == "approved":
            result = state.get("result", {})
            st.success(f"Approved. Personalised messages written to {result.get('outbox_file')}")
            st.dataframe(pd.DataFrame(result.get("previews", [])), hide_index=True)

with tab_history:
    rows = api("GET", "/campaigns") or []
    st.dataframe(pd.DataFrame(rows), hide_index=True) if rows else st.info("No finished campaigns yet.")

with tab_analytics:
    bench = api("GET", "/analytics/benchmarks") or []
    if bench:
        df = pd.DataFrame(bench).set_index("channel")
        st.markdown("#### Channel benchmarks")
        st.dataframe(df)
        st.bar_chart(df[["avg_open_rate", "avg_ctr", "avg_conversion_rate"]])
    perf = api("GET", "/analytics/campaigns") or []
    if perf:
        st.markdown("#### Past campaigns")
        st.dataframe(pd.DataFrame(perf), hide_index=True)
