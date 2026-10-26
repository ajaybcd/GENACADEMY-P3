"""Credential-safe provider diagnostics. Never display raw exception bodies."""
from urllib.parse import urlparse

import httpx
from langchain_openai import ChatOpenAI

from .domain import ReviewError


def create_model(model_name: str, key: str, endpoint: str):
    """Use native Responses for OpenAI tool loops; keep compatible providers on chat."""
    validate_endpoint(endpoint)
    native = urlparse(endpoint).hostname == "api.openai.com"
    options = dict(model=model_name.strip(), api_key=key.strip(), base_url=endpoint,
                   max_retries=0, timeout=60, max_tokens=5000, use_responses_api=native)
    if native and model_name.startswith(("gpt-5", "gpt-6", "o3", "o4")):
        options["reasoning"] = {"effort": "low"}
    return ChatOpenAI(**options)


def validate_endpoint(endpoint: str):
    parsed = urlparse(endpoint)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ReviewError("Use an HTTPS model API base URL without embedded credentials, query or fragment.")


def provider_failure(error: Exception) -> str:
    status = getattr(error, "status_code", None)
    body = getattr(error, "body", {})
    body = body if isinstance(body, dict) else {}
    detail = body.get("error", body)
    detail = detail if isinstance(detail, dict) else {}
    code = getattr(error, "code", None) or detail.get("code")
    param = getattr(error, "param", None) or detail.get("param")
    codes = {"model_not_found", "invalid_api_key", "insufficient_quota", "rate_limit_exceeded",
             "unsupported_parameter", "unsupported_value", "invalid_request_error", "invalid_tool_schema"}
    params = {"model", "max_tokens", "max_completion_tokens", "temperature", "tools", "messages", "reasoning_effort", "response_format"}
    prefix = f"Provider HTTP {status}. " if isinstance(status, int) else ""
    messages = {
        401: "Authentication failed. Check that this is an active OpenAI API key, without spaces.",
        403: "Access denied. Check the API project's model and endpoint permissions.",
        404: "Model or endpoint is unavailable to this project. Check the model ID and connection model list.",
        429: "Quota or rate limit reached. Check API project billing and usage limits.",
        400: "Provider rejected the request configuration.",
        422: "Provider rejected the request schema.",
    }
    text = prefix + messages.get(status, "Model request failed. Check provider connectivity and tool support.")
    if isinstance(code, str) and code in codes:
        text += f" Code: {code}."
    if isinstance(param, str) and param in params:
        text += f" Parameter: {param}."
    if not status and isinstance(error, (TypeError, ValueError)):
        text = "Local model or tool configuration failed validation before a provider response."
    return text


def check_connection(endpoint: str, key: str) -> list[str]:
    validate_endpoint(endpoint)
    if not key:
        raise ReviewError("Enter the model API key first, then press Tab.")
    try:
        with httpx.Client(timeout=20, follow_redirects=False) as client:
            response = client.get(endpoint.rstrip("/") + "/models", headers={"Authorization": f"Bearer {key}"})
        if response.status_code != 200:
            # Only the status is shown; raw response bodies may echo credentials.
            error = ReviewError()
            error.status_code = response.status_code
            raise ReviewError(provider_failure(error))
        payload = response.json()
        names = [m["id"] for m in payload.get("data", []) if isinstance(m, dict) and isinstance(m.get("id"), str)]
        return sorted(n for n in names if len(n) <= 120 and n.isascii())
    except (httpx.RequestError, ValueError, AttributeError):
        raise ReviewError("Connection check failed. Check the API base URL and network access.") from None
