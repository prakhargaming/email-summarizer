import base64
from typing import Annotated

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode

from app.core import ssm


class Settings(BaseSettings):
    database_url: str
    google_client_id: str
    google_client_secret: str
    token_encryption_key: bytes
    allowed_emails: Annotated[frozenset[str], NoDecode]
    env: str = "prod"
    dev_user_email: str | None = None
    llm_model: str
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    gemini_api_key: str | None = None

    @field_validator("token_encryption_key", mode="before")
    @classmethod
    def _decode_key(cls, v: str) -> bytes:
        key = base64.b64decode(v, validate=True)
        if len(key) != 32:
            raise ValueError("TOKEN_ENCRYPTION_KEY must be base64 of 32 bytes")
        return key

    @field_validator("allowed_emails", mode="before")
    @classmethod
    def _split_emails(cls, v: str) -> frozenset[str]:
        return frozenset(e.strip().lower() for e in v.split(",") if e.strip())

    @model_validator(mode="after")
    def _dev_email_only_in_dev(self) -> "Settings":
        if self.dev_user_email and self.env != "dev":
            raise ValueError("DEV_USER_EMAIL is only allowed when ENV=dev")
        return self

    @model_validator(mode="after")
    def _llm_key_present(self) -> "Settings":
        if self.llm_base_url and not self.llm_api_key:
            raise ValueError("LLM_API_KEY is required when LLM_BASE_URL is set")
        if not self.llm_base_url and not self.gemini_api_key:
            raise ValueError("GEMINI_API_KEY is required when LLM_BASE_URL is unset")
        return self


ssm.load_into_env()
settings = Settings()
