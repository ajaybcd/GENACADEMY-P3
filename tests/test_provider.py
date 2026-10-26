import httpx
import pytest
from uuid import uuid4

from review_app.demo import demo_review
from review_app.provider import check_connection, provider_failure
from review_app.workflow import build_workflow


@pytest.mark.parametrize("status,code,param,expected", [
    (401, "invalid_api_key", None, "Authentication failed"),
    (404, "model_not_found", "model", "Model or endpoint"),
    (429, "insufficient_quota", None, "Quota or rate limit"),
    (400, "unsupported_parameter", "max_tokens", "Parameter: max_tokens"),
])
def test_safe_diagnostic(status, code, param, expected):
    error = RuntimeError("secret-in-raw-message")
    error.status_code = status
    error.body = {"error": {"code": code, "param": param, "message": "secret-in-body"}}
    text = provider_failure(error)
    assert expected in text
    assert "secret" not in text


def test_unknown_exception_fields_cannot_expose_secret():
    error = RuntimeError("secret-in-message")
    error.body = {"code": "secret-key", "param": "secret-param"}
    assert "secret" not in provider_failure(error)


def test_provider_failure_stops_following_specialists_and_does_not_interrupt():
    class FailingModel:
        def bind_tools(self, tools):
            return self

        def invoke(self, messages):
            error = RuntimeError("secret-in-message")
            error.status_code = 404
            error.body = {"code": "model_not_found"}
            raise error

    snapshot, _ = demo_review()
    snapshot.demo = False
    graph = build_workflow(None, FailingModel(), None)
    config = {"configurable": {"thread_id": uuid4().hex}}
    graph.invoke({"snapshot": snapshot.as_dict(), "run_id": uuid4().hex}, config)
    state = graph.get_state(config)
    assert state.values["status"] == "analysis_failed"
    assert state.values["model_calls"] == 1
    assert not state.next
    assert any("model_not_found" in w for w in state.values["warnings"])
    assert all("secret" not in w for w in state.values["warnings"])


def test_connection_requires_key_before_any_network_call():
    with pytest.raises(Exception, match="Enter the model API key"):
        check_connection("https://api.openai.com/v1", "")


def test_native_openai_uses_responses_with_supported_reasoning_setting():
    from review_app.provider import create_model
    from langchain_core.messages import HumanMessage
    from langchain_core.tools import tool

    @tool
    def inspect_code(path: str) -> str:
        """Inspect a source file."""
        return path

    model = create_model("gpt-5.6-terra", "dummy-construction-key", "https://api.openai.com/v1")
    bound = model.bind_tools([inspect_code])
    # Inspect the SDK-bound wire payload without making a network request.
    payload = model._get_request_payload([HumanMessage(content="Review this change")], **bound.kwargs)
    assert model.use_responses_api is True
    assert payload["reasoning"] == {"effort": "low"}
    assert "reasoning_effort" not in payload
    assert payload["tools"][0]["type"] == "function"
    assert payload["tools"][0]["name"] == "inspect_code"
    assert "input" in payload and "messages" not in payload
