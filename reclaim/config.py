import os
from typing import Literal, Optional
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

# Load from .env if present
load_dotenv(override=False)


class Settings(BaseSettings):
    """
    Centralized configuration for RECLAIM.
    Reads from environment variables and local .env file.
    """
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Provider strategy: "auto" | "gemini" | "groq" | "mock"
    llm_provider: Literal["auto", "gemini", "groq", "mock"] = Field(
        default="auto",
        alias="LLM_PROVIDER"
    )

    # API Keys
    gemini_api_key: Optional[str] = Field(default=None, alias="GEMINI_API_KEY")
    google_api_key: Optional[str] = Field(default=None, alias="GOOGLE_API_KEY")
    groq_api_key: Optional[str] = Field(default=None, alias="GROQ_API_KEY")

    # Current verified active models (single source of truth)
    gemini_model: str = Field(default="gemini-2.0-flash", alias="GEMINI_MODEL")
    groq_model: str = Field(default="openai/gpt-oss-120b", alias="GROQ_MODEL")
    groq_fallback_model: str = Field(default="openai/gpt-oss-20b", alias="GROQ_FALLBACK_MODEL")

    # Request economy & resilience
    llm_max_retries: int = Field(default=2, alias="LLM_MAX_RETRIES")
    llm_timeout_seconds: float = Field(default=25.0, alias="LLM_TIMEOUT_SECONDS")

    def get_effective_gemini_key(self) -> Optional[str]:
        return self.gemini_api_key or self.google_api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")

    def get_effective_groq_key(self) -> Optional[str]:
        return self.groq_api_key or os.getenv("GROQ_API_KEY")


def sanitize_secret(secret: Optional[str]) -> str:
    """
    Returns a masked version of an API key for safe logging.
    Never exposes real keys in audit trails or console output.
    """
    if not secret:
        return "[NOT SET]"
    if len(secret) <= 8:
        return "***"
    return f"{secret[:4]}...{secret[-4:]}"


# Global settings singleton
settings = Settings()
