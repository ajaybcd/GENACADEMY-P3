"""Run with: python -m streamlit run app.py"""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import streamlit as st
from langgraph.types import Command

from review_app.demo import demo_review
from review_app.domain import PRRef, ReviewError
from review_app.github import GitHub
from review_app.ledger import Ledger
from review_app.provider import check_connection, create_model, validate_endpoint
from review_app.workflow import build_workflow

ROOT = Path(__file__).resolve().parent
st.set_page_config(page_title="Review Workbench", page_icon="🔎", layout="wide")
st.markdown("""<style>
.block-container {max-width:1150px; padding-top:4rem;}
[data-testid="stColumn"] h3 {font-size:1.4rem;}
[data-testid="stMetric"] {border:1px solid #d9e2e9; border-radius:12px; padding:14px;}
</style>""", unsafe_allow_html=True)


def setting(name, fallback=""):
    value = os.environ.get(name)
    if value:
        return value
    try:
        return str(st.secrets.get(name, fallback))
    except FileNotFoundError:
        return fallback


def ledger():
    return Ledger(ROOT / ".review_state" / "posts.sqlite3")


st.caption("WEEK 3  /  CODE REVIEW AGENT")
st.title("A second set of eyes for your pull request")
st.write("Three specialists investigate correctness, security and missing tests. You decide what gets posted.")

with st.sidebar:
    st.header("Review setup")
    mode = st.radio("Source", ["Offline demo", "GitHub pull request"])
    live = mode == "GitHub pull request"
    pr_url = st.text_input("Pull request URL", placeholder="https://github.com/owner/repo/pull/123", disabled=not live)
    with st.expander("Connection settings", expanded=live):
        model_name = st.text_input("Model name", value=setting("MODEL_NAME", "gpt-5.6-terra"), placeholder="Your provider's tool-capable model ID")
        endpoint = st.text_input("Model API base URL", value=setting("MODEL_BASE_URL", "https://api.openai.com/v1"))
        key_input = st.text_input("Model API key", type="password", help="Optional if MODEL_API_KEY or OPENAI_API_KEY is configured.")
        token_input = st.text_input("GitHub token", type="password", help="Public reads can be anonymous. Posting requires Pull requests: write; private source also needs Contents: read.")
        key = key_input or setting("MODEL_API_KEY") or setting("OPENAI_API_KEY")
        token = token_input or setting("GITHUB_TOKEN")
        if live and st.button("Check model connection"):
            try:
                available = check_connection(endpoint, key)
                if model_name.strip() in available:
                    st.success("API key accepted; the selected model is listed for this project.")
                else:
                    st.warning("API key accepted, but the selected model is not in the returned list. Choose a listed chat model with tool support.")
                chat_models = [n for n in available if n.startswith(("gpt-", "o3", "o4")) and not any(t in n for t in ("audio", "realtime", "image", "transcribe", "tts"))]
                st.write("Available chat model IDs:")
                st.code("\n".join(chat_models[:60]) or "No matching chat model IDs were returned.")
            except ReviewError as error:
                st.error(str(error))
    st.caption("Python-first • up to 15 reviewed files • bounded model calls")
    if live:
        st.caption("Starting a live review sends selected repository source to the configured model provider.")
    start = st.button("Start review" if live else "Explore demo", type="primary", use_container_width=True)
    if not live:
        st.caption("Illustrative fixtures. No API key, model calls or GitHub writes.")

if start:
    try:
        if live:
            ref = PRRef.parse(pr_url)
            validate_endpoint(endpoint)
            if not key or not model_name.strip():
                raise ReviewError("Provide a model API key and a tool-capable model name for live analysis.")
        previous = st.session_state.pop("run", None)
        if previous and previous["github"]:
            previous["github"].close()
        gh = None
        with st.status("Review in progress", expanded=True) as progress:
            if live:
                st.write("Loading the pull request and pinning its source commit…")
                gh = GitHub(token)
                snapshot = gh.snapshot(ref)
                fixtures = None
                model = create_model(model_name, key, endpoint)
            else:
                snapshot, fixtures = demo_review()
                model = None
            run_id = uuid4().hex
            config = {"configurable": {"thread_id": run_id}}

            def show_event(item):
                st.write(f"{item['specialist']} · {item['detail']}")

            graph = build_workflow(gh, model, ledger(), demo_candidates=fixtures, event=show_event)
            graph.invoke({"snapshot": snapshot.as_dict(), "run_id": run_id}, config)
            # Detach UI callbacks after analysis; graph resumes only approval and posting.
            st.session_state.run = {"graph": graph, "config": config, "github": gh, "can_post": bool(token) and live}
            failed = graph.get_state(config).values.get("analysis_failed", False)
            progress.update(label="Model analysis failed" if failed else "Review ready for your decision",
                            state="error" if failed else "complete", expanded=failed)
    except ReviewError as error:
        if "gh" in locals() and gh:
            gh.close()
        st.error(str(error))
    except Exception:
        if "gh" in locals() and gh:
            gh.close()
        st.error("Review could not complete. Check connection settings and provider tool support, then try again.")

active = st.session_state.get("run")
if not active:
    st.divider()
    a, b, c = st.columns(3)
    a.subheader("01  Investigate")
    a.write("Specialists choose tools to inspect changes, source structure and relevant context.")
    b.subheader("02  Verify")
    b.write("Findings need exact source evidence on an added line of the pinned diff.")
    c.subheader("03  Decide")
    c.write("Select useful findings, export the report, or explicitly approve a GitHub review.")
    st.info("Choose Explore demo to try the full approval flow without connecting any account.")
else:
    state = dict(active["graph"].get_state(active["config"]).values)
    snapshot = state["snapshot"]
    findings = state["findings"]
    demo = snapshot["demo"]
    run_id = state["run_id"]
    status = state["status"]
    awaiting = status == "awaiting_approval"
    if demo:
        st.info("Offline demonstration: these findings are fixtures, not AI analysis or real evaluation results.")
    else:
        st.link_button("Open source pull request", snapshot["url"])
    st.subheader(snapshot["title"])
    st.caption(f"Run {run_id} · Commit {snapshot['head_sha'][:12]} · {status.replace('_', ' ')}")
    a, b, c = st.columns(3)
    a.metric("Validated findings", len(findings))
    b.metric("High or critical", sum(f["severity"] in {"Critical", "High"} for f in findings))
    c.metric("Model calls", state["model_calls"])

    review_tab, activity_tab, source_tab = st.tabs(["Review and approve", "Activity and limits", "Source evidence"])
    selected = []
    with review_tab:
        if state["warnings"]:
            st.warning("This review has limitations. Inspect the Activity and limits tab before approving.")
        if status == "analysis_failed":
            st.error("Model analysis failed. Zero findings is not a completed review. Inspect the provider error under Activity and limits, then check the model connection.")
        elif not findings:
            st.write("No validated findings were returned. This does not establish that the code is free of issues.")
        for finding in findings:
            with st.expander(f"{finding['severity']} · {finding['category']} · {finding['path']}:{finding['line']}", expanded=True):
                st.code(finding["snippet"], language="python")
                st.write(finding["body"])
                if st.checkbox("Include in review", key=f"{run_id}-{finding['id']}", disabled=not awaiting):
                    selected.append(finding["id"])
        if awaiting:
            acknowledged = st.checkbox("I reviewed the findings and any listed limitations.", key=f"ack-{run_id}")
            left, right = st.columns(2)
            approve = left.button("Approve demo selection" if demo else "Approve selected and post to GitHub", type="primary",
                                  disabled=not (selected and acknowledged and (demo or active["can_post"])))
            reject = right.button("Finish without posting")
            if not demo and not active["can_post"]:
                st.caption("Posting is unavailable for this run because it started without a GitHub token. You can export the report.")
            if approve or reject:
                with st.spinner("Applying your decision…"):
                    try:
                        active["graph"].invoke(Command(resume={"action": "post" if approve else "reject", "approved_ids": selected}), active["config"])
                    except Exception:
                        st.error("Decision could not complete. Inspect the run status before retrying any write.")
                    else:
                        st.rerun()
        elif status == "posted":
            st.success("Approved review posted to GitHub.")
            st.link_button("Open posted review", state["review_url"])
        elif status == "demo_approved_no_post":
            st.success("Demo selection approved. No GitHub post was made.")
        elif status == "stale_pr":
            st.error("The PR changed after analysis. Approval is invalid; start a new review.")
        elif status == "post_uncertain":
            st.error("Posting was attempted but its result is uncertain. Reconcile this run before starting another review for the same PR.")
            if st.button("Check GitHub for this run's review"):
                try:
                    url = active["github"].find_review(PRRef.parse(snapshot["url"]), run_id)
                    if url:
                        ledger().complete(run_id, url)
                        active["graph"].update_state(active["config"], {"status": "posted", "review_url": url})
                        st.rerun()
                    else:
                        st.warning("No review with this run marker was found. Posting remains blocked; inspect GitHub manually before any new run.")
                except ReviewError as error:
                    st.error(str(error))
        elif status in {"post_blocked", "closed_pr", "invalid_approval", "validation_failed"}:
            st.error(f"Posting blocked: {status.replace('_', ' ')}. Inspect the limitations and start a new review if appropriate.")
        elif status != "analysis_failed":
            st.write("Review finished without posting.")
        report = {k: state.get(k) for k in ["run_id", "findings", "warnings", "trace", "model_calls", "approved_ids", "status", "review_url"]}
        report.update({"source_url": snapshot["url"], "head_sha": snapshot["head_sha"], "demo": demo})
        st.download_button("Download review report", json.dumps(report, indent=2), file_name=f"review-{run_id[:8]}.json", mime="application/json")
    with activity_tab:
        st.write("Correctness → Security → Tests → deterministic consolidation → human approval → batched post")
        st.caption("Each specialist chooses its own tools. Tool names and progress are shown; private model reasoning is not collected.")
        for warning in state["warnings"]:
            st.warning(warning)
        for item in state["trace"]:
            st.text(f"{item['specialist']} · {item['event']} · {item['detail']}")
        st.caption("Review checkpoints last for this app session/process. A local post ledger survives restarts. Hosted storage may be ephemeral.")
    with source_tab:
        for path, source in snapshot["files"].items():
            with st.expander(path):
                st.code(source["patch"], language="diff")
