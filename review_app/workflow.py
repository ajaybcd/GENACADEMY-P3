from __future__ import annotations

from typing import TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .agents import FOCUS, run_specialist
from .domain import PRRef, ReviewError, Snapshot, validate_findings
from .provider import provider_failure


class ReviewState(TypedDict, total=False):
    snapshot: dict
    run_id: str
    candidates: list[dict]
    findings: list[dict]
    warnings: list[str]
    trace: list[dict]
    model_calls: int
    approved_ids: list[str]
    decision: str
    status: str
    review_url: str
    analysis_failed: bool


def build_workflow(github, model, ledger, demo_candidates=None, event=None):
    """No agent has a write tool. Only this graph's post node can write after interrupt."""
    def analyze(state: ReviewState):
        snapshot = Snapshot.from_dict(state["snapshot"])
        candidates, warnings, trace = [], list(snapshot.skipped), []
        budget = {"calls": 0}
        failed = False
        if snapshot.demo:
            candidates = demo_candidates or []
            trace = [{"specialist": c, "event": "fixture", "detail": "Offline fixture; no model was called"} for c in FOCUS]
        else:
            for category in FOCUS:
                try:
                    proposed, issues = run_specialist(category, snapshot, model, github, budget, trace, event)
                    candidates.extend(proposed)
                    warnings.extend(issues)
                except Exception as error:
                    # Provider exceptions may contain keys or request content; never show raw text.
                    warnings.append(f"{category} failed. {provider_failure(error)}")
                    warnings.append("Remaining specialist calls were stopped. Fix the connection or model settings before starting a new review.")
                    failed = True
                    break
        return dict(candidates=candidates, warnings=warnings, trace=trace, model_calls=budget["calls"], analysis_failed=failed)

    def consolidate(state: ReviewState):
        findings, dropped = validate_findings(state["candidates"], Snapshot.from_dict(state["snapshot"]))
        return {"findings": findings, "warnings": state["warnings"] + [f"Dropped finding: {d}" for d in dropped],
                "status": "analysis_failed" if state.get("analysis_failed") else "awaiting_approval"}

    def approval(state: ReviewState):
        decision = interrupt({"findings": state["findings"], "head_sha": state["snapshot"]["head_sha"],
                              "warnings": state["warnings"], "run_id": state["run_id"]})
        if not isinstance(decision, dict) or decision.get("action") != "post":
            return {"decision": "reject", "approved_ids": [], "status": "closed_without_post"}
        ids = decision.get("approved_ids", [])
        allowed = {f["id"] for f in state["findings"]}
        if not isinstance(ids, list) or any(not isinstance(i, str) or i not in allowed for i in ids):
            return {"decision": "reject", "approved_ids": [], "status": "invalid_approval"}
        return {"decision": "post", "approved_ids": list(dict.fromkeys(ids))}

    def post(state: ReviewState):
        if state["decision"] != "post" or not state["approved_ids"]:
            return {"status": "closed_without_post"}
        snapshot = Snapshot.from_dict(state["snapshot"])
        if snapshot.demo:
            return {"status": "demo_approved_no_post"}
        run_id = state["run_id"]
        existing = ledger.get(run_id)
        if existing:
            return {"status": "posted" if existing[0] == "posted" else "post_uncertain",
                    "review_url": existing[1] or ""}
        selected = [f for f in state["findings"] if f["id"] in state["approved_ids"]]
        validated, dropped = validate_findings(selected, snapshot)
        if dropped or len(validated) != len(selected):
            return {"status": "validation_failed"}
        ref = PRRef.parse(snapshot.url)
        try:
            metadata = github.get(ref.api_path)
            if metadata["head"]["sha"] != snapshot.head_sha or metadata["base"]["sha"] != snapshot.base_sha:
                return {"status": "stale_pr"}
            if metadata.get("state") != "open":
                return {"status": "closed_pr"}
            if not ledger.claim(run_id):
                return {"status": "post_uncertain"}
            url = github.post_review(ref, snapshot.head_sha, validated, run_id)
            ledger.complete(run_id, url)
            return {"status": "posted", "review_url": url}
        except ReviewError as error:
            return {"status": "post_uncertain" if ledger.get(run_id) else "post_blocked",
                    "warnings": state["warnings"] + [str(error)]}

    builder = StateGraph(ReviewState)
    for name, node in [("analyze", analyze), ("consolidate", consolidate), ("approval", approval), ("post", post)]:
        builder.add_node(name, node)
    builder.add_edge(START, "analyze")
    builder.add_edge("analyze", "consolidate")
    builder.add_conditional_edges("consolidate", lambda state: END if state.get("analysis_failed") else "approval")
    builder.add_edge("approval", "post")
    builder.add_edge("post", END)
    return builder.compile(checkpointer=InMemorySaver())
