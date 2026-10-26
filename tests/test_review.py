import base64
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage
from langgraph.types import Command

from review_app.agents import run_specialist
from review_app.demo import demo_review
from review_app.domain import PRRef, ReviewError, SourceFile, added_lines, anchor, validate_findings
from review_app.github import GitHub
from review_app.ledger import Ledger
from review_app.workflow import build_workflow


@pytest.fixture
def store():
    # Windows sandbox may make mode-700 temp dirs unreadable; use ordinary project dirs.
    return Ledger(Path(".review_state") / "tests" / uuid4().hex / "ledger.sqlite3")


class FakeGitHub:
    def __init__(self, snapshot, failure=False):
        self.snapshot = snapshot
        self.failure = failure
        self.posts = []
        self.head = snapshot.head_sha
        self.base = snapshot.base_sha

    def get(self, path):
        return {"head": {"sha": self.head}, "base": {"sha": self.base}, "state": "open"}

    def post_review(self, ref, sha, findings, run_id):
        self.posts.append((sha, findings, run_id))
        if self.failure:
            raise ReviewError("Uncertain post.")
        return "https://github.com/acme/project/pull/1#pullrequestreview-1"


class ScriptedModel:
    """Exercise real specialist graphs without network or paid model calls."""
    def __init__(self, findings, never_finish=False):
        self.findings = findings
        self.never_finish = never_finish

    def bind_tools(self, tools):
        parent = self

        class Bound:
            turns = 0

            def invoke(self, messages):
                self.turns += 1
                category = next(c for c in ["Correctness", "Security", "Tests"] if f"{c} specialist" in messages[0].content)
                if self.turns == 1 or parent.never_finish:
                    name, args = "list_changed_files", {}
                elif self.turns == 2:
                    # A failed read must become an observation and allow another turn.
                    name, args = "read_patch", {"path": "does-not-exist.py"}
                else:
                    name = "submit_findings"
                    args = {"findings": [f for f in parent.findings if f["category"] == category]}
                return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call-{self.turns}"}])

        return Bound()


def live_graph(store, failure=False, run_id=None):
    snapshot, candidates = demo_review()
    snapshot.demo = False
    snapshot.url = "https://github.com/acme/project/pull/1"
    gh = FakeGitHub(snapshot, failure)
    graph = build_workflow(gh, ScriptedModel(candidates), store)
    run_id = run_id or uuid4().hex
    config = {"configurable": {"thread_id": uuid4().hex}}
    graph.invoke({"snapshot": snapshot.as_dict(), "run_id": run_id}, config)
    return graph, config, gh


@pytest.mark.parametrize("url", ["http://github.com/a/b/pull/1", "https://github.com.evil/a/b/pull/1",
                                      "https://github.com/a/b/pull/0", "https://github.com/a/b/pull/1?x=1",
                                      "https://github.com/../b/pull/1"])
def test_invalid_pr_urls(url):
    with pytest.raises(ReviewError):
        PRRef.parse(url)


def test_multi_hunk_line_mapping():
    patch = "@@ -1,2 +1,3 @@\n a\n+b\n c\n@@ -8 +9 @@\n-old\n+new"
    assert added_lines(patch) == {2, 9}


def test_truncated_patch_rejected():
    with pytest.raises(ReviewError):
        added_lines("@@ -0,0 +1,2 @@\n+only_one")


def test_ambiguous_and_unchanged_snippets_rejected():
    with pytest.raises(ReviewError):
        anchor(SourceFile("x.py", "@@ -0,0 +1,2 @@\n+same\n+same", "same\nsame"), "same")
    with pytest.raises(ReviewError):
        anchor(SourceFile("x.py", "@@ -1 +1,2 @@\n stable\n+new", "stable\nnew"), "stable")


def test_validation_sorts_deduplicates_and_drops_bad_evidence():
    snapshot, candidates = demo_review()
    findings, dropped = validate_findings(candidates + [candidates[0], {**candidates[0], "snippet": "made up"}], snapshot)
    assert len(findings) == 3
    assert findings[0]["severity"] == "High"
    assert len(dropped) == 1


def test_no_write_before_approval_and_only_selected_findings_post(store):
    graph, config, gh = live_graph(store)
    state = graph.get_state(config)
    assert state.next == ("approval",)
    assert not gh.posts
    assert state.values["model_calls"] == 9
    assert len(state.values["findings"]) == 3
    selected = state.values["findings"][0]["id"]
    graph.invoke(Command(resume={"action": "post", "approved_ids": [selected]}), config)
    assert graph.get_state(config).values["status"] == "posted"
    assert len(gh.posts) == 1
    assert [f["id"] for f in gh.posts[0][1]] == [selected]


@pytest.mark.parametrize("decision", [{"action": "reject"}, {"action": "post", "approved_ids": ["forged"]},
                                       {"action": "post", "approved_ids": []}])
def test_rejection_and_invalid_selection_never_write(store, decision):
    graph, config, gh = live_graph(store)
    graph.invoke(Command(resume=decision), config)
    assert not gh.posts


@pytest.mark.parametrize("field", ["head", "base"])
def test_changed_commit_invalidates_approval(store, field):
    graph, config, gh = live_graph(store)
    setattr(gh, field, "changed")
    selected = graph.get_state(config).values["findings"][0]["id"]
    graph.invoke(Command(resume={"action": "post", "approved_ids": [selected]}), config)
    assert graph.get_state(config).values["status"] == "stale_pr"
    assert not gh.posts


def test_uncertain_post_cannot_be_repeated_across_graph_instances(store):
    run_id = uuid4().hex
    graph, config, gh = live_graph(store, failure=True, run_id=run_id)
    selected = graph.get_state(config).values["findings"][0]["id"]
    graph.invoke(Command(resume={"action": "post", "approved_ids": [selected]}), config)
    assert graph.get_state(config).values["status"] == "post_uncertain"
    assert len(gh.posts) == 1
    second, config2, gh2 = live_graph(store, run_id=run_id)
    second.invoke(Command(resume={"action": "post", "approved_ids": [selected]}), config2)
    assert second.get_state(config2).values["status"] == "post_uncertain"
    assert not gh2.posts


def test_specialist_model_budget_stops_unfinished_loop():
    snapshot, candidates = demo_review()
    budget, trace = {"calls": 0}, []
    findings, warnings = run_specialist("Security", snapshot, ScriptedModel(candidates, never_finish=True), None, budget, trace)
    assert budget["calls"] == 8
    assert not findings and warnings


def test_demo_approval_never_posts(store):
    snapshot, candidates = demo_review()
    graph = build_workflow(None, None, store, demo_candidates=candidates)
    config = {"configurable": {"thread_id": uuid4().hex}}
    graph.invoke({"snapshot": snapshot.as_dict(), "run_id": uuid4().hex}, config)
    selected = graph.get_state(config).values["findings"][0]["id"]
    graph.invoke(Command(resume={"action": "post", "approved_ids": [selected]}), config)
    assert graph.get_state(config).values["status"] == "demo_approved_no_post"


def test_github_post_payload_is_single_batched_comment_review():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"html_url": "https://github.com/a/b/pull/1#review"})

    client = httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handle))
    gh = GitHub(client=client)
    snapshot, candidates = demo_review()
    findings, _ = validate_findings(candidates, snapshot)
    gh.post_review(PRRef("a", "b", 1), "pinned-sha", findings, "run-marker")
    import json
    payload = json.loads(requests[0].content)
    assert len(requests) == 1 and requests[0].method == "POST"
    assert payload["commit_id"] == "pinned-sha" and payload["event"] == "COMMENT"
    assert len(payload["comments"]) == 3
    assert all(c["side"] == "RIGHT" and c["body"].startswith("[AI Review]") for c in payload["comments"])


def test_github_read_recovers_from_transient_failure(monkeypatch):
    monkeypatch.setattr("review_app.github.time.sleep", lambda _: None)
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(503 if len(requests) < 3 else 200, json={"ok": True})

    gh = GitHub(client=httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handle)))
    assert gh.get("/test") == {"ok": True}
    assert len(requests) == 3


def test_snapshot_loads_pinned_source_and_reports_skipped_files():
    requested = []
    metadata = {"title": "PR", "changed_files": 2, "state": "open",
                "head": {"sha": "sha1", "repo": {"full_name": "a/b"}}, "base": {"sha": "sha0"}}

    def handle(request):
        requested.append(request)
        if request.url.path.endswith("/files"):
            return httpx.Response(200, json=[{"filename": "x.py", "status": "added", "patch": "@@ -0,0 +1 @@\n+x = 1"},
                                             {"filename": "readme.txt", "status": "modified"}])
        if "/contents/" in request.url.path:
            assert request.url.params["ref"] == "sha1"
            return httpx.Response(200, json={"type": "file", "encoding": "base64", "size": 6,
                                            "content": base64.b64encode(b"x = 1\n").decode()})
        return httpx.Response(200, json=metadata)

    gh = GitHub(client=httpx.Client(base_url="https://api.github.com", transport=httpx.MockTransport(handle)))
    snapshot = gh.snapshot(PRRef("a", "b", 1))
    assert snapshot.files["x.py"].content == "x = 1\n"
    assert snapshot.skipped and snapshot.head_sha == "sha1"
