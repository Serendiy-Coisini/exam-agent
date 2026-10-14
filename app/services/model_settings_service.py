"""Local model configuration. Secrets stay on the server and out of responses."""
import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings
from app.services.analysis_skill_service import get_analysis_skill_status
from app.services.model_provider_options import model_request_options
from app.schemas.model_settings_schema import ModelSettingsPayload

MODEL_SETTINGS_FILE = Path(__file__).resolve().parents[2] / "data" / "model_settings.json"
_CONFIG_LOCK = threading.RLock()


@dataclass(frozen=True)
class ModelConfiguration:
    base_url: str
    model_name: str
    api_key: str = field(repr=False)


def get_model_configuration() -> ModelConfiguration:
    with _CONFIG_LOCK:
        return ModelConfiguration(settings.LLM_BASE_URL, settings.LLM_MODEL_NAME, settings.LLM_API_KEY)


def _apply(config: ModelConfiguration) -> None:
    settings.LLM_BASE_URL = config.base_url
    settings.LLM_MODEL_NAME = config.model_name
    settings.LLM_API_KEY = config.api_key


def get_model_status() -> dict:
    config = get_model_configuration()
    return {"base_url": config.base_url, "model_name": config.model_name,
            "api_key_set": bool(config.api_key),
            "configured": bool(config.api_key and config.model_name),
            "analysis_skill": get_analysis_skill_status()}


def _resolve(payload: ModelSettingsPayload) -> ModelConfiguration:
    current = get_model_configuration()
    key = payload.api_key.get_secret_value() if payload.api_key else ""
    if payload.clear_api_key:
        key = ""
    elif not key:
        if current.api_key and payload.base_url != current.base_url.rstrip("/"):
            raise ValueError("更换接口地址时，请重新填写 API Key，或选择清除已保存的密钥")
        key = current.api_key
    return ModelConfiguration(payload.base_url, payload.model_name, key)


def save_model_settings(payload: ModelSettingsPayload) -> dict:
    with _CONFIG_LOCK:
        config = _resolve(payload)
        MODEL_SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            # A private temporary file followed by replace prevents partial writes.
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                             dir=MODEL_SETTINGS_FILE.parent,
                                             prefix=".model-settings-", delete=False) as out:
                temporary = Path(out.name)
                os.chmod(temporary, 0o600)
                json.dump({"base_url": config.base_url, "model_name": config.model_name,
                           "api_key": config.api_key}, out, ensure_ascii=False)
                out.flush()
                os.fsync(out.fileno())
            os.replace(temporary, MODEL_SETTINGS_FILE)
            _apply(config)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return get_model_status()


def load_model_settings() -> None:
    if not MODEL_SETTINGS_FILE.exists():
        return
    try:
        data = json.loads(MODEL_SETTINGS_FILE.read_text(encoding="utf-8"))
        payload = ModelSettingsPayload.model_validate(data)
        # Persisted blank keys intentionally override an environment key.
        config = ModelConfiguration(payload.base_url, payload.model_name,
                                    payload.api_key.get_secret_value() if payload.api_key else "")
        with _CONFIG_LOCK:
            _apply(config)
    except (OSError, ValueError, TypeError):
        logging.getLogger(__name__).warning("本地模型配置读取失败，请在模型设置中重新保存。")


def test_model_connection(payload: ModelSettingsPayload) -> dict:
    config = _resolve(payload)
    if not config.api_key:
        raise ValueError("请填写 API Key 后再测试连接")
    started = time.monotonic()
    try:
        from openai import OpenAI
        with OpenAI(api_key=config.api_key, base_url=config.base_url,
                    timeout=min(settings.LLM_TIMEOUT_SECONDS, 10), max_retries=0) as client:
            response = client.chat.completions.create(
                model=config.model_name, messages=[{"role": "user", "content": "请只回复 OK。"}],
                max_tokens=16,
                **model_request_options(config, connection_test=True),
            )
        if not response.choices or not (response.choices[0].message.content or "").strip():
            return {"ok": False, "message": "接口返回内容为空，请检查模型是否支持 Chat Completions。"}
    except Exception as exc:
        # Never return provider error bodies: they can contain credentials or headers.
        from openai import APIConnectionError, APITimeoutError, AuthenticationError, NotFoundError, RateLimitError
        if isinstance(exc, AuthenticationError):
            message = "认证失败，请检查 API Key 和接口地址。"
        elif isinstance(exc, RateLimitError):
            message = "服务限流或额度不足，请检查账户额度后重试。"
        elif isinstance(exc, NotFoundError):
            message = "接口或模型不存在，请检查基础地址和模型名称。"
        elif isinstance(exc, APITimeoutError):
            message = "连接超时，请检查服务状态或网络后重试。"
        elif isinstance(exc, APIConnectionError):
            message = "无法连接模型服务，请检查接口地址和网络。"
        else:
            message = "连接测试失败，请检查接口是否兼容 Chat Completions 及模型是否可用。"
        return {"ok": False, "message": message}
    return {"ok": True, "message": "连接成功，模型已返回有效响应。",
            "elapsed_ms": round((time.monotonic() - started) * 1000)}
