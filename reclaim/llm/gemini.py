import json
import logging
from typing import Optional, Dict, Any
import httpx

from reclaim.llm.base import LLMProvider, UsageStats
from reclaim.llm.exceptions import (
    ProviderAuthError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderUnavailableError,
    LLMError,
    ModelRefusalError
)
from reclaim.config import sanitize_secret

logger = logging.getLogger(__name__)

GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider(LLMProvider):
    """
    Google Gemini provider implementing structured generation and error classification.
    Uses direct REST API via HTTPX to avoid deprecated SDK dependencies.
    """

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-2.0-flash",
        timeout_seconds: float = 25.0
    ):
        super().__init__(model_name=model_name, timeout_seconds=timeout_seconds)
        if not api_key:
            raise ProviderAuthError("Gemini API key cannot be empty", provider="gemini")
        self.api_key = api_key

    @property
    def provider_name(self) -> str:
        return "gemini"

    async def _call_raw_api(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        json_schema: Optional[Dict[str, Any]] = None
    ) -> tuple[str, UsageStats]:
        endpoint = f"{GEMINI_API_BASE}/{self.model_name}:generateContent"

        # Build request body
        body: Dict[str, Any] = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": prompt}]
                }
            ],
            "generationConfig": {
                "temperature": 0.1,
            }
        }

        if system_prompt:
            body["systemInstruction"] = {
                "role": "system",
                "parts": [{"text": system_prompt}]
            }

        if json_schema:
            body["generationConfig"]["responseMimeType"] = "application/json"

        # Header auth or query param
        params = {"key": self.api_key}
        masked_key = sanitize_secret(self.api_key)

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    endpoint,
                    params=params,
                    json=body,
                    headers={"Content-Type": "application/json"}
                )

        except httpx.TimeoutException as exc:
            raise ProviderUnavailableError(
                f"Gemini API request timed out after {self.timeout_seconds}s",
                provider="gemini",
                raw_error=exc
            )
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(
                f"Gemini API connection error: {str(exc)}",
                provider="gemini",
                raw_error=exc
            )

        # Classify HTTP error codes
        status_code = response.status_code
        if status_code == 200:
            data = response.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise ModelRefusalError("Gemini returned empty candidates", provider="gemini")

            candidate = candidates[0]
            finish_reason = candidate.get("finishReason", "UNKNOWN")
            if finish_reason in ["SAFETY", "RECITATION", "BLOCKLIST"]:
                raise ModelRefusalError(
                    f"Gemini generation blocked due to {finish_reason}",
                    provider="gemini"
                )

            parts = candidate.get("content", {}).get("parts", [])
            if not parts:
                raise ModelRefusalError("Gemini candidate contains no content parts", provider="gemini")

            text_content = parts[0].get("text", "")

            # Token tracking
            usage_meta = data.get("usageMetadata", {})
            usage = UsageStats(
                prompt_tokens=usage_meta.get("promptTokenCount", 0),
                completion_tokens=usage_meta.get("candidatesTokenCount", 0),
                total_tokens=usage_meta.get("totalTokenCount", 0)
            )
            return text_content, usage

        # Non-200 Error Classification
        try:
            err_json = response.json().get("error", {})
            err_message = err_json.get("message", response.text)
            err_status = err_json.get("status", "")
        except Exception:
            err_message = response.text
            err_status = ""

        # Sanitize any key leakage in error text
        clean_msg = err_message.replace(self.api_key, masked_key)

        if status_code in (401, 403) and ("API_KEY_INVALID" in err_message or "PERMISSION_DENIED" in err_status):
            raise ProviderAuthError(f"Gemini authentication failed: {clean_msg}", provider="gemini")

        if status_code == 429 or "RESOURCE_EXHAUSTED" in err_status:
            if "quota" in err_message.lower():
                raise ProviderQuotaError(f"Gemini quota exhausted: {clean_msg}", provider="gemini")
            raise ProviderRateLimitError(f"Gemini rate limit exceeded: {clean_msg}", provider="gemini")

        if status_code == 404:
            raise ProviderUnavailableError(
                f"Gemini model '{self.model_name}' not found or deprecated: {clean_msg}",
                provider="gemini"
            )

        if status_code in (500, 502, 503, 504):
            raise ProviderUnavailableError(f"Gemini service unavailable ({status_code}): {clean_msg}", provider="gemini")

        raise LLMError(f"Gemini API error ({status_code}): {clean_msg}", provider="gemini")
