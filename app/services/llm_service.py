import json
import time
from contextlib import contextmanager
from contextvars import ContextVar

_ANALYSIS_DEADLINE = ContextVar("analysis_deadline", default=None)


@contextmanager
def inference_budget(seconds):
    token = _ANALYSIS_DEADLINE.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _ANALYSIS_DEADLINE.reset(token)


from app.core.config import settings
from app.services.model_provider_options import model_request_options
from app.services.model_settings_service import get_model_configuration


def generate_llm_json(prompt: str, fallback_data: dict) -> dict:
    """Ask the configured chat model for one JSON object."""
    config = get_model_configuration()
    fallback = dict(fallback_data or {})
    if not config.api_key or not config.model_name:
        fallback["llm_used"] = False
        fallback["model"] = config.model_name or ""
        fallback["error"] = "LLM is not configured."
        return fallback

    try:
        from openai import OpenAI
    except ImportError:
        fallback["llm_used"] = False
        fallback["model"] = config.model_name or ""
        fallback["error"] = "OpenAI SDK is not installed."
        return fallback

    try:
        client = _build_client(OpenAI, config)
        response = client.chat.completions.create(
            model=config.model_name,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the planning brain for a Chinese civil-service exam advisor. "
                        "Return exactly one valid JSON object. Do not use markdown."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            **model_request_options(config, temperature=0),
        )
        data = _parse_json_object(response.choices[0].message.content or "")
    except Exception:
        fallback["llm_used"] = False
        fallback["model"] = config.model_name or ""
        fallback["error"] = "LLM JSON planning failed."
        return fallback

    if not data:
        fallback["llm_used"] = False
        fallback["model"] = config.model_name or ""
        fallback["error"] = "LLM JSON planning returned invalid content."
        return fallback

    data["llm_used"] = True
    data["model"] = config.model_name or ""
    data.setdefault("error", "")
    return data


def generate_llm_chat(
    system_prompt: str,
    messages: list[dict],
    fallback_report: str,
    temperature: float = 0.45,
) -> dict:
    """Generate a natural answer with recent conversation context."""
    config = get_model_configuration()
    if not config.api_key:
        return _fallback_result(
            fallback_report=fallback_report,
            model_name=config.model_name,
            error="LLM_API_KEY is not configured; returned fallback answer.",
        )

    if not config.model_name:
        return _fallback_result(
            fallback_report=fallback_report,
            model_name=config.model_name,
            error="LLM_MODEL_NAME is not configured; returned fallback answer.",
        )

    try:
        from openai import OpenAI
    except ImportError:
        return _fallback_result(
            fallback_report=fallback_report,
            model_name=config.model_name,
            error="OpenAI Python SDK is not installed; returned fallback answer.",
        )

    try:
        client = _build_client(OpenAI, config)
        response = client.chat.completions.create(
            model=config.model_name,
            messages=[
                {"role": "system", "content": system_prompt},
                *_normalize_chat_messages(messages),
            ],
            **model_request_options(config, temperature=temperature),
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception:
        return _fallback_result(
            fallback_report=fallback_report,
            model_name=config.model_name,
            error="LLM chat call failed; returned fallback answer.",
        )

    if not content:
        return _fallback_result(
            fallback_report=fallback_report,
            model_name=config.model_name,
            error="LLM chat returned empty content; returned fallback answer.",
        )

    return {
        "summary": content,
        "analysis_report": content,
        "llm_used": True,
        "model": config.model_name or "",
        "error": "",
    }


def generate_llm_summary(prompt: str, fallback_report: str) -> dict:
    """Generate the tool-grounded recommendation report used by existing flows."""
    return generate_llm_chat(
        system_prompt=(
            "You are a careful Chinese exam-position advisor. Only use the provided "
            "tool results to discuss job recommendations, risk, scores, and checks. "
            "Do not invent jobs, score lines, competition data, notices, or policy sources."
        ),
        messages=[{"role": "user", "content": prompt}],
        fallback_report=fallback_report,
        temperature=0.2,
    )


def _build_client(openai_class, config=None):
    """Create an OpenAI-compatible SDK client."""
    config = config or get_model_configuration()
    timeout = settings.LLM_TIMEOUT_SECONDS
    deadline = _ANALYSIS_DEADLINE.get()
    if deadline is not None:
        timeout = min(timeout, deadline - time.monotonic())
        if timeout <= 0:
            raise TimeoutError("Analysis model budget exhausted")
    client_kwargs = {"api_key": config.api_key, "timeout": timeout, "max_retries": 0}
    if config.base_url:
        client_kwargs["base_url"] = config.base_url
    return openai_class(**client_kwargs)


def _parse_json_object(content: str) -> dict:
    text = (content or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _normalize_chat_messages(messages: list[dict]) -> list[dict]:
    normalized = []
    for message in messages[-12:]:
        role = str(message.get("role") or "").strip()
        content = str(message.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        normalized.append({"role": role, "content": content})
    return normalized


def _fallback_result(fallback_report: str, error: str, model_name: str | None = None) -> dict:
    safe_report = (fallback_report or "").strip()
    if not safe_report:
        safe_report = "暂时无法生成有效回答，请稍后再试。"

    return {
        "summary": safe_report,
        "analysis_report": safe_report,
        "llm_used": False,
        "model": settings.LLM_MODEL_NAME if model_name is None else model_name,
        "error": error,
    }
