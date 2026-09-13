from typing import Optional


class LLMError(Exception):
    """Base exception for all RECLAIM LLM provider errors."""
    def __init__(self, message: str, provider: str = "unknown", raw_error: Optional[Exception] = None):
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.raw_error = raw_error

    def __str__(self) -> str:
        return f"[{self.provider.upper()}] {self.message}"


class ProviderAuthError(LLMError):
    """Raised when authentication fails (invalid API key or unauthorized)."""
    pass


class ProviderQuotaError(LLMError):
    """Raised when quota/credits are completely exhausted for the provider."""
    pass


class ProviderRateLimitError(LLMError):
    """Raised when requests exceed the rate limit (HTTP 429)."""
    def __init__(self, message: str, provider: str = "unknown", retry_after: Optional[float] = None, raw_error: Optional[Exception] = None):
        super().__init__(message, provider=provider, raw_error=raw_error)
        self.retry_after = retry_after


class ProviderUnavailableError(LLMError):
    """Raised when the provider service is down, unreachable, or times out."""
    pass


class SchemaValidationError(LLMError):
    """Raised when LLM output cannot be parsed into the expected Pydantic schema."""
    def __init__(self, message: str, provider: str = "unknown", raw_text: str = "", raw_error: Optional[Exception] = None):
        super().__init__(message, provider=provider, raw_error=raw_error)
        self.raw_text = raw_text


class ProviderConfigurationError(LLMError):
    """Raised when provider configuration is invalid or missing required credentials."""
    pass


class ModelRefusalError(LLMError):
    """Raised when the model explicitly refuses or triggers safety blocks."""
    pass
