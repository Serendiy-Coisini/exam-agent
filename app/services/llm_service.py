"""Model calls with backwards-compatible results and request-local stage budgets.

``inference_budget(seconds)`` creates a hard outer deadline. Optional work can
use ``model_stage(name, reserve_seconds=N)`` to leave N seconds for a later
stage; ``budget_remaining()`` returns usable seconds for the current stage, or
None outside a budget. Nested budgets cannot extend their parent's deadline.

Existing generation signatures and llm_used remain compatible. Visible output
adoption is decided by grounding, not by this transport layer. model_metadata
records attempted/produced_text separately; failure_reason contains a stable,
sanitized code and never includes provider exception bodies or credentials.
"""
import json
import time
from contextlib import contextmanager
from contextvars import ContextVar

from app.agents.prompt_builder import build_job_analysis_system_prompt
from app.core.config import settings
from app.services.model_provider_options import model_request_options
from app.services.model_settings_service import get_model_configuration


_ANALYSIS_DEADLINE = ContextVar("analysis_deadline", default=None)
_MODEL_STAGE = ContextVar("model_stage", default=("", 0.0))
_FAILURE_MESSAGES = {
    "missing_configuration": "LLM is not configured; returned fallback answer.",
    "sdk_unavailable": "OpenAI SDK is not installed; returned fallback answer.",
    "budget_exhausted": "Analysis model budget exhausted; returned fallback answer.",
    "provider_failure": "LLM provider call failed; returned fallback answer.",
    "invalid_json": "LLM returned invalid JSON; returned fallback answer.",
    "invalid_schema": "LLM returned an invalid object schema; returned fallback answer.",
    "empty_response": "LLM returned empty content; returned fallback answer.",
}


class ModelBudgetExhausted(TimeoutError):
    """The request or optional stage has no time available for a model call."""


@contextmanager
def inference_budget(seconds):
    deadline = time.monotonic() + max(0.0, float(seconds))
    parent = _ANALYSIS_DEADLINE.get()
    token = _ANALYSIS_DEADLINE.set(min(parent, deadline) if parent is not None else deadline)
    try:
        yield
    finally:
        _ANALYSIS_DEADLINE.reset(token)


@contextmanager
def model_stage(name, reserve_seconds=0):
    """Name a model stage and protect time needed by subsequent visible work."""
    parent_reserve = _MODEL_STAGE.get()[1]
    token = _MODEL_STAGE.set((str(name or ""), max(parent_reserve, 0.0, float(reserve_seconds))))
    try:
        yield
    finally:
        _MODEL_STAGE.reset(token)


def budget_remaining(reserve_seconds=0):
    """Return usable stage time, or None when no outer deadline is active."""
    deadline = _ANALYSIS_DEADLINE.get()
    if deadline is None:
        return None
    reserve = max(_MODEL_STAGE.get()[1], 0.0, float(reserve_seconds))
    return max(0.0, deadline - time.monotonic() - reserve)


def _metadata(config, start, attempted, produced_text, failure_reason):
    return {
        "stage": _MODEL_STAGE.get()[0], "attempted": bool(attempted),
        "produced_text": bool(produced_text), "model": config.model_name or "",
        "failure_reason": failure_reason,
        "elapsed_seconds": round(max(0.0, time.monotonic() - start), 4),
        "budget_remaining_seconds": budget_remaining(),
    }


def _preflight(config):
    remaining = budget_remaining()
    if remaining is not None and remaining <= 0:
        return "budget_exhausted"
    if not config.api_key or not config.model_name:
        return "missing_configuration"
    return ""


def _provider_failure(exc):
    remaining = budget_remaining()
    return "budget_exhausted" if isinstance(exc, ModelBudgetExhausted) or (remaining is not None and remaining <= 0) else "provider_failure"


def _with_metadata(result, config, start, attempted=False, produced_text=False, reason=""):
    result.update({"model": config.model_name or "", "failure_reason": reason,
                   "model_metadata": _metadata(config, start, attempted, produced_text, reason)})
    return result


def generate_llm_json(prompt: str, fallback_data: dict, analysis_system_prompt: str = "") -> dict:
    """Ask for a JSON object; malformed content has an explicit failure reason.

    Returned keys may be partial for legacy callers. Supplied keys with a known
    fallback type must match that type; semantic/evidence checks happen later.
    """
    start = time.monotonic()
    config = get_model_configuration()
    fallback = dict(fallback_data or {})
    attempted = produced_text = False

    def fail(reason):
        result = {**fallback, "llm_used": False, "error": _FAILURE_MESSAGES[reason]}
        return _with_metadata(result, config, start, attempted, produced_text, reason)

    reason = _preflight(config)
    if reason:
        return fail(reason)
    try:
        from openai import OpenAI
    except ImportError:
        return fail("sdk_unavailable")
    try:
        client = _build_client(OpenAI, config)
        attempted = True
        response = client.chat.completions.create(
            model=config.model_name,
            messages=[
                {"role": "system", "content": (
                    "You are the planning brain for a Chinese civil-service exam advisor. "
                    "Return exactly one valid JSON object. Do not use markdown."
                    + ("\n\n" + analysis_system_prompt if analysis_system_prompt else ""))},
                {"role": "user", "content": prompt},
            ],
            **model_request_options(config, temperature=0),
        )
        content = response.choices[0].message.content or ""
        produced_text = isinstance(content, str) and bool(content.strip())
    except Exception as exc:
        return fail(_provider_failure(exc))
    if budget_remaining() is not None and budget_remaining() <= 0:
        return fail("budget_exhausted")
    if not produced_text:
        return fail("empty_response")
    data, reason = _decode_json_object(content)
    if reason:
        return fail(reason)
    if not _compatible_schema(data, fallback):
        return fail("invalid_schema")
    data.update({"llm_used": True, "error": ""})
    return _with_metadata(data, config, start, attempted, produced_text)


def generate_llm_chat(system_prompt: str, messages: list[dict], fallback_report: str,
                      temperature: float = 0.45) -> dict:
    """Generate natural text and distinguish transport failure from adoption."""
    start = time.monotonic()
    config = get_model_configuration()
    attempted = produced_text = False

    def fail(reason):
        result = _fallback_result(fallback_report, _FAILURE_MESSAGES[reason], config.model_name)
        return _with_metadata(result, config, start, attempted, produced_text, reason)

    reason = _preflight(config)
    if reason:
        return fail(reason)
    try:
        from openai import OpenAI
    except ImportError:
        return fail("sdk_unavailable")
    try:
        client = _build_client(OpenAI, config)
        attempted = True
        response = client.chat.completions.create(
            model=config.model_name,
            messages=[{"role": "system", "content": system_prompt}, *_normalize_chat_messages(messages)],
            **model_request_options(config, temperature=temperature),
        )
        raw_content = response.choices[0].message.content or ""
        content = raw_content.strip() if isinstance(raw_content, str) else ""
        produced_text = bool(content)
    except Exception as exc:
        return fail(_provider_failure(exc))
    if budget_remaining() is not None and budget_remaining() <= 0:
        return fail("budget_exhausted")
    if not content:
        return fail("empty_response")
    return _with_metadata({"summary": content, "analysis_report": content, "llm_used": True, "error": ""},
                          config, start, attempted, produced_text)


def generate_llm_summary(prompt: str, fallback_report: str, use_skill: bool = True) -> dict:
    """Generate the tool-grounded recommendation report used by existing flows."""
    return generate_llm_chat(
        system_prompt=build_job_analysis_system_prompt(use_skill=use_skill),
        messages=[{"role": "user", "content": prompt}],
        fallback_report=fallback_report, temperature=0.2,
    )


def _build_client(openai_class, config=None):
    """Build from one configuration snapshot, capped at the usable stage time."""
    config = config or get_model_configuration()
    timeout = settings.LLM_TIMEOUT_SECONDS
    remaining = budget_remaining()
    if remaining is not None:
        if remaining <= 0:
            raise ModelBudgetExhausted("Analysis model budget exhausted")
        timeout = min(timeout, remaining)
    client_kwargs = {"api_key": config.api_key, "timeout": timeout, "max_retries": 0}
    if config.base_url:
        client_kwargs["base_url"] = config.base_url
    return openai_class(**client_kwargs)


def _decode_json_object(content):
    text = content.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return {}, "invalid_json"
        try:
            value = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return {}, "invalid_json"
    return (value, "") if isinstance(value, dict) and value else ({}, "invalid_schema")


def _compatible_schema(data, fallback):
    for key, value in data.items():
        expected = fallback.get(key)
        if expected is None:
            continue
        if isinstance(expected, bool):
            if not isinstance(value, bool):
                return False
        elif isinstance(expected, (str, list, dict)):
            if not isinstance(value, type(expected)):
                return False
            if isinstance(expected, list) and expected and any(not isinstance(item, type(expected[0])) for item in value):
                return False
        elif isinstance(expected, (int, float)) and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            return False
    return True


def _parse_json_object(content: str) -> dict:
    """Legacy parser interface retained for existing consumers and mocks."""
    return _decode_json_object(content or "")[0]


def _normalize_chat_messages(messages: list[dict]) -> list[dict]:
    normalized = []
    for message in messages[-12:]:
        role = str(message.get("role") or "").strip()
        content = str(message.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            normalized.append({"role": role, "content": content})
    return normalized


def _fallback_result(fallback_report: str, error: str, model_name: str | None = None) -> dict:
    safe_report = (fallback_report or "").strip() or "暂时无法生成有效回答，请稍后再试。"
    return {"summary": safe_report, "analysis_report": safe_report, "llm_used": False,
            "model": settings.LLM_MODEL_NAME if model_name is None else model_name, "error": error}
