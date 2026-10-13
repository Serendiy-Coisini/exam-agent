from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class ModelSettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    base_url: str = Field(..., min_length=1, max_length=2048)
    model_name: str = Field(..., min_length=1, max_length=200)
    api_key: SecretStr | None = None
    clear_api_key: bool = False

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        try:
            url = urlsplit(value)
            valid = (url.scheme in {"http", "https"} and url.hostname
                     and not url.username and not url.password
                     and not url.query and not url.fragment and url.port != 0
                     and not any(c.isspace() or ord(c) < 32 for c in value))
        except ValueError:
            valid = False
        if not valid:
            raise ValueError("请输入有效的 HTTP 或 HTTPS 接口基础地址，不含密钥、参数或片段")
        if url.path.rstrip("/").endswith(("/chat/completions", "/responses")):
            raise ValueError("请填写接口基础地址，例如 https://api.example.com/v1，不含 /chat/completions")
        return value

    @field_validator("model_name")
    @classmethod
    def validate_model_name(cls, value: str) -> str:
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("请填写有效的模型名称")
        return value

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        key = value.get_secret_value().strip() if value is not None else ""
        if len(key) > 4096 or any(c.isspace() or ord(c) < 32 for c in key):
            raise ValueError("API Key 格式无效")
        return SecretStr(key) if key else None

    @model_validator(mode="after")
    def validate_clear(self):
        if self.clear_api_key and self.api_key:
            raise ValueError("清除密钥时请勿同时填写新密钥")
        return self
