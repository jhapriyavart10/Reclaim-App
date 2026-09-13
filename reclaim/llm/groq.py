import asyncio
import copy
import hashlib
import json
import logging
import re
import time
from typing import Optional, Dict, Any, List, Tuple
from pydantic import BaseModel, Field
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

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODELS_URL = "https://api.groq.com/openai/v1/models"
MAX_RETRY_WINDOW_SECONDS = 60.0


def to_strict_json_schema(schema_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transforms a JSON Schema (e.g. from Pydantic) into a strict JSON Schema
    strictly conforming to Groq/OpenAI Structured Outputs specifications:
    1. Every object schema MUST have additionalProperties: false.
    2. Every property declared in properties MUST be listed in required.
    3. Remove 'default', 'minItems', 'minLength', 'minimum', 'maximum' keywords
       (unsupported in strict mode).
    4. Traverses nested definitions, $defs, items, and anyOf/allOf/oneOf.
    """
    d = copy.deepcopy(schema_dict)

    # Keywords forbidden or unsupported in strict structured output mode
    FORBIDDEN_KEYWORDS = {"default", "minItems", "minLength", "minimum", "maximum"}

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            # Remove forbidden keywords at every level
            for kw in FORBIDDEN_KEYWORDS:
                node.pop(kw, None)

            # Check if this node represents an object schema
            if node.get("type") == "object" or "properties" in node:
                node["additionalProperties"] = False
                props = node.get("properties", {})
                if isinstance(props, dict):
                    node["required"] = list(props.keys())
                    # Remove forbidden keywords from each property
                    for prop_schema in props.values():
                        if isinstance(prop_schema, dict):
                            for kw in FORBIDDEN_KEYWORDS:
                                prop_schema.pop(kw, None)

            # Recurse into all dictionary values
            for k, v in list(node.items()):
                walk(v)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(d)
    return d


def classify_rate_limit(
    status_code: int,
    headers: Dict[str, str],
    error_payload: Dict[str, Any]
) -> str:
    """
    Evidence-based rate-limit classifier.
    Distinguishes between:
    - RATE_LIMIT_REQUEST
    - RATE_LIMIT_TOKEN
    - RATE_LIMIT_DAILY_REQUEST
    - RATE_LIMIT_DAILY_TOKEN
    - RATE_LIMIT_UNKNOWN
    """
    if status_code != 429:
        return "NONE"

    err_msg = (error_payload.get("message") or "").lower()
    err_type = (error_payload.get("type") or "").lower()

    # Normalize header keys to lowercase
    h_lower = {k.lower(): v for k, v in headers.items()}
    rem_tokens_hdr = h_lower.get("x-ratelimit-remaining-tokens")
    reset_tokens_hdr = h_lower.get("x-ratelimit-reset-tokens", "").lower()
    rem_req_hdr = h_lower.get("x-ratelimit-remaining-requests")
    reset_req_hdr = h_lower.get("x-ratelimit-reset-requests", "").lower()

    # 1. Check explicit error message text for token vs request & daily
    if "tokens per day" in err_msg or "tpd" in err_msg:
        return "RATE_LIMIT_DAILY_TOKEN"
    if "requests per day" in err_msg or "rpd" in err_msg:
        return "RATE_LIMIT_DAILY_REQUEST"
    if "tokens per minute" in err_msg or "tpm" in err_msg:
        return "RATE_LIMIT_TOKEN"
    if "requests per minute" in err_msg or "rpm" in err_msg:
        return "RATE_LIMIT_REQUEST"

    # 2. Check header evidence
    if rem_tokens_hdr is not None and str(rem_tokens_hdr).strip() == "0":
        if "h" in reset_tokens_hdr or "d" in reset_tokens_hdr:
            return "RATE_LIMIT_DAILY_TOKEN"
        return "RATE_LIMIT_TOKEN"

    if rem_req_hdr is not None and str(rem_req_hdr).strip() == "0":
        if "h" in reset_req_hdr or "d" in reset_req_hdr:
            return "RATE_LIMIT_DAILY_REQUEST"
        return "RATE_LIMIT_REQUEST"

    # 3. Check error type field
    if err_type == "tokens":
        return "RATE_LIMIT_TOKEN"
    if err_type == "requests":
        return "RATE_LIMIT_REQUEST"

    return "RATE_LIMIT_UNKNOWN"


class LLMTraceRecord(BaseModel):
    """Machine-readable audit trace record for every LLM invocation."""
    provider: str = "groq"
    model: str
    request_id: str = ""
    status: str  # "SUCCESS", "429_RATE_LIMIT", "400_VALIDATION_ERROR", "ERROR"
    status_code: int
    latency_ms: float
    retry_count: int
    reasoning_status: str
    decision_source: str
    validation_result: str
    fallback_used: bool
    fallback_reason: Optional[str] = None
    estimated_input_tokens: int = 0
    estimated_output_tokens: int = 0
    rate_limit_classification: str = "NONE"
    rate_limit_headers: Dict[str, str] = Field(default_factory=dict)


class GroqProvider(LLMProvider):
    """
    Groq provider with:
    - openai/gpt-oss-120b primary and openai/gpt-oss-20b fallback
    - strict JSON schema structured outputs
    - evidence-based 429 rate limit diagnostics (TPM, TPD, RPM, RPD)
    - bounded exponential backoff & instant failover for prolonged 429s
    - request deduplication and per-run budget enforcement
    - comprehensive machine-readable telemetry tracing
    """

    def __init__(
        self,
        api_key: str,
        model_name: str = "openai/gpt-oss-120b",
        timeout_seconds: float = 25.0,
        fallback_model: Optional[str] = "openai/gpt-oss-20b",
        max_requests_per_run: int = 15,
        max_tokens_per_run: int = 35000
    ):
        if model_name in ("gpt-oss-120b", "gpt-oss-20b"):
            model_name = f"openai/{model_name}"
        if fallback_model in ("gpt-oss-120b", "gpt-oss-20b"):
            fallback_model = f"openai/{fallback_model}"
        super().__init__(model_name=model_name, timeout_seconds=timeout_seconds)
        if not api_key:
            raise ProviderAuthError("Groq API key cannot be empty", provider="groq")
        self.api_key = api_key
        self.fallback_model = fallback_model

        # Budgets
        self.max_requests_per_run = max_requests_per_run
        self.max_tokens_per_run = max_tokens_per_run
        self.requests_used = 0
        self.tokens_used = 0

        # Deduplication cache: key -> (raw_text, usage)
        self._dedup_cache: Dict[str, Tuple[str, UsageStats]] = {}

        # Traces
        self.traces: List[LLMTraceRecord] = []

        # Verified models cache
        self._verified_models: Optional[Dict[str, bool]] = None

    @property
    def provider_name(self) -> str:
        return "groq"

    async def verify_models(self) -> Dict[str, bool]:
        """Verify model IDs against Groq /openai/v1/models endpoint."""
        if self._verified_models is not None:
            return self._verified_models

        headers = {"Authorization": f"Bearer {self.api_key}"}
        result = {self.model_name: False}
        if self.fallback_model:
            result[self.fallback_model] = False

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.get(GROQ_MODELS_URL, headers=headers)
            if res.status_code == 200:
                available_ids = {m["id"] for m in res.json().get("data", [])}
                for m in result:
                    result[m] = (m in available_ids)
            self._verified_models = result
        except Exception as e:
            logger.warning(f"[GROQ] Model verification query failed: {e}")
            self._verified_models = result

        return self._verified_models

    def _estimate_tokens(self, text: str) -> int:
        """Heuristic token estimation (~4 chars per token)."""
        return max(1, len(text) // 4)

    def _make_cache_key(self, model: str, prompt: str, schema: Optional[Dict[str, Any]]) -> str:
        schema_part = json.dumps(schema, sort_keys=True) if schema else ""
        raw = f"{model}:{prompt}:{schema_part}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def _call_raw_api(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        json_schema: Optional[Dict[str, Any]] = None
    ) -> Tuple[str, UsageStats]:
        # Enforce request budget
        if self.requests_used >= self.max_requests_per_run:
            raise ProviderQuotaError(
                f"Per-run LLM request budget exceeded ({self.requests_used}/{self.max_requests_per_run}).",
                provider="groq"
            )
        if self.tokens_used >= self.max_tokens_per_run:
            raise ProviderQuotaError(
                f"Per-run LLM token budget exceeded ({self.tokens_used}/{self.max_tokens_per_run}).",
                provider="groq"
            )

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        masked_key = sanitize_secret(self.api_key)

        models_to_try = [self.model_name]
        if self.fallback_model and self.fallback_model != self.model_name:
            models_to_try.append(self.fallback_model)

        last_error: Optional[Exception] = None
        est_input_tokens = sum(self._estimate_tokens(m["content"]) for m in messages)

        for model_idx, active_model in enumerate(models_to_try):
            is_fallback = (model_idx > 0)
            cache_key = self._make_cache_key(active_model, prompt, json_schema)

            # Deduplication check
            if cache_key in self._dedup_cache:
                logger.info(f"[GROQ] Deduplicated request hit cache for model {active_model}.")
                cached_text, cached_usage = self._dedup_cache[cache_key]
                self.last_model_used = active_model
                self.last_fallback_used = is_fallback
                return cached_text, cached_usage

            body: Dict[str, Any] = {
                "model": active_model,
                "messages": messages,
                "temperature": 0.1,
                "max_tokens": 1500,
            }

            if json_schema:
                # Use strict JSON Schema structured outputs mode
                strict_schema = to_strict_json_schema(json_schema)
                schema_name = json_schema.get("title", "ResponseSchema")
                body["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema_name,
                        "schema": strict_schema,
                        "strict": True
                    }
                }

            max_retries = 2
            for attempt in range(max_retries + 1):
                t_start = time.perf_counter()
                try:
                    async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                        response = await client.post(
                            GROQ_API_URL,
                            json=body,
                            headers=headers
                        )
                except httpx.TimeoutException as exc:
                    raise ProviderUnavailableError(
                        f"Groq API request timed out after {self.timeout_seconds}s",
                        provider="groq",
                        raw_error=exc
                    )
                except httpx.RequestError as exc:
                    raise ProviderUnavailableError(
                        f"Groq API connection error: {str(exc)}",
                        provider="groq",
                        raw_error=exc
                    )

                elapsed_ms = (time.perf_counter() - t_start) * 1000.0
                status_code = response.status_code

                # Extract sanitized rate limit headers
                resp_headers_lower = {k.lower(): v for k, v in response.headers.items()}
                rate_limit_hdrs = {
                    "retry-after": resp_headers_lower.get("retry-after", ""),
                    "x-ratelimit-limit-requests": resp_headers_lower.get("x-ratelimit-limit-requests", ""),
                    "x-ratelimit-remaining-requests": resp_headers_lower.get("x-ratelimit-remaining-requests", ""),
                    "x-ratelimit-limit-tokens": resp_headers_lower.get("x-ratelimit-limit-tokens", ""),
                    "x-ratelimit-remaining-tokens": resp_headers_lower.get("x-ratelimit-remaining-tokens", ""),
                    "x-ratelimit-reset-requests": resp_headers_lower.get("x-ratelimit-reset-requests", ""),
                    "x-ratelimit-reset-tokens": resp_headers_lower.get("x-ratelimit-reset-tokens", ""),
                }
                request_id = response.headers.get("x-request-id", "")

                if status_code == 200:
                    data = response.json()
                    choices = data.get("choices", [])
                    if not choices:
                        raise ModelRefusalError("Groq returned empty choices", provider="groq")

                    message = choices[0].get("message", {})
                    text_content = message.get("content", "")

                    usage_meta = data.get("usage", {})
                    usage = UsageStats(
                        prompt_tokens=usage_meta.get("prompt_tokens", est_input_tokens),
                        completion_tokens=usage_meta.get("completion_tokens", self._estimate_tokens(text_content)),
                        total_tokens=usage_meta.get("total_tokens", est_input_tokens + self._estimate_tokens(text_content)),
                        latency_ms=elapsed_ms
                    )

                    self.requests_used += 1
                    self.tokens_used += usage.total_tokens

                    self.last_request_id = request_id
                    self.last_model_used = active_model
                    self.last_fallback_used = is_fallback
                    self.last_rate_limit_class = "NONE"

                    # Record trace
                    trace = LLMTraceRecord(
                        provider="groq",
                        model=active_model,
                        request_id=request_id,
                        status="SUCCESS",
                        status_code=200,
                        latency_ms=elapsed_ms,
                        retry_count=attempt,
                        reasoning_status="LLM_REASONING",
                        decision_source=f"groq:{active_model}",
                        validation_result="PASS",
                        fallback_used=is_fallback,
                        fallback_reason=None if not is_fallback else "rate_limit_failover",
                        estimated_input_tokens=usage.prompt_tokens,
                        estimated_output_tokens=usage.completion_tokens,
                        rate_limit_classification="NONE",
                        rate_limit_headers=rate_limit_hdrs
                    )
                    self.traces.append(trace)

                    # Store in deduplication cache
                    self._dedup_cache[cache_key] = (text_content, usage)
                    return text_content, usage

                # Handle errors
                try:
                    err_json = response.json().get("error", {})
                    err_message = err_json.get("message", response.text)
                    err_type = err_json.get("type", "")
                    err_code = err_json.get("code", "")
                except Exception:
                    err_json = {}
                    err_message = response.text
                    err_type = ""
                    err_code = ""

                clean_msg = err_message.replace(self.api_key, masked_key)
                rate_limit_class = classify_rate_limit(status_code, response.headers, err_json)

                # Log comprehensive telemetry for failed call
                logger.warning(
                    f"[GROQ TELEMETRY] Model={active_model} Status={status_code} "
                    f"ReqId={request_id} Class={rate_limit_class} Error={clean_msg[:150]}"
                )

                if status_code == 401:
                    raise ProviderAuthError(f"Groq authentication failed: {clean_msg}", provider="groq")

                if status_code == 400:
                    # 400 json_validate_failed or schema error
                    trace = LLMTraceRecord(
                        provider="groq",
                        model=active_model,
                        request_id=request_id,
                        status="400_VALIDATION_ERROR",
                        status_code=400,
                        latency_ms=elapsed_ms,
                        retry_count=attempt,
                        reasoning_status="FAILED",
                        decision_source=f"groq:{active_model}",
                        validation_result="FAIL",
                        fallback_used=is_fallback,
                        fallback_reason="json_validation_failed",
                        estimated_input_tokens=est_input_tokens,
                        estimated_output_tokens=0,
                        rate_limit_classification="NONE",
                        rate_limit_headers=rate_limit_hdrs
                    )
                    self.traces.append(trace)
                    logger.error(
                        f"[GROQ 400 DIAGNOSTICS] Model: {active_model}, Code: {err_code}, "
                        f"Type: {err_type}, PromptSize: {len(prompt)} chars, Error: {clean_msg}"
                    )
                    raise LLMError(
                        f"Groq 400 schema/validation error (code={err_code}, type={err_type}): {clean_msg}",
                        provider="groq"
                    )

                if status_code == 429:
                    trace = LLMTraceRecord(
                        provider="groq",
                        model=active_model,
                        request_id=request_id,
                        status="429_RATE_LIMIT",
                        status_code=429,
                        latency_ms=elapsed_ms,
                        retry_count=attempt,
                        reasoning_status="DEGRADED_REASONING",
                        decision_source=f"groq:{active_model}",
                        validation_result="SKIPPED",
                        fallback_used=is_fallback,
                        fallback_reason=rate_limit_class,
                        estimated_input_tokens=est_input_tokens,
                        estimated_output_tokens=0,
                        rate_limit_classification=rate_limit_class,
                        rate_limit_headers=rate_limit_hdrs
                    )
                    self.traces.append(trace)

                    # Determine wait time from retry-after header or message regex
                    wait_sec: Optional[float] = None
                    retry_hdr = rate_limit_hdrs.get("retry-after", "")
                    if retry_hdr and retry_hdr.isdigit():
                        wait_sec = float(retry_hdr)
                    else:
                        m_sec = re.search(r"try again in ([\d\.]+)s", clean_msg)
                        m_min_sec = re.search(r"try again in ([\d\.]+)m([\d\.]+)s", clean_msg)
                        if m_min_sec:
                            wait_sec = float(m_min_sec.group(1)) * 60.0 + float(m_min_sec.group(2))
                        elif m_sec:
                            wait_sec = float(m_sec.group(1))

                    # If daily exhaustion or wait is clearly longer than MAX_RETRY_WINDOW_SECONDS
                    if rate_limit_class in ("RATE_LIMIT_DAILY_TOKEN", "RATE_LIMIT_DAILY_REQUEST") or (wait_sec is not None and wait_sec > MAX_RETRY_WINDOW_SECONDS):
                        logger.warning(
                            f"[GROQ 429] Prolonged rate-limit on {active_model} ({rate_limit_class}, wait={wait_sec}s > {MAX_RETRY_WINDOW_SECONDS}s). "
                            f"Immediately triggering fallback failover."
                        )
                        last_error = ProviderRateLimitError(
                            f"Groq {rate_limit_class} on {active_model}: {clean_msg}",
                            provider="groq"
                        )
                        break  # Immediately break retry loop to try next candidate model

                    # Bounded exponential backoff if wait is reasonable and retries remain
                    if attempt < max_retries:
                        sleep_time = wait_sec + 0.5 if wait_sec is not None else min(2.0 * (2 ** attempt), MAX_RETRY_WINDOW_SECONDS)
                        sleep_time = min(sleep_time, MAX_RETRY_WINDOW_SECONDS)
                        logger.info(f"[GROQ 429] Rate limit hit on {active_model}. Backing off {sleep_time:.2f}s before attempt {attempt + 2}...")
                        await asyncio.sleep(sleep_time)
                        continue

                    # Retries exhausted for this model
                    last_error = ProviderRateLimitError(
                        f"Groq rate limit exceeded on {active_model} after {max_retries + 1} attempts ({rate_limit_class}): {clean_msg}",
                        provider="groq"
                    )
                    break

                if status_code == 404 or "model_decommissioned" in err_code or "model_not_found" in err_code:
                    raise ProviderUnavailableError(
                        f"Groq model '{active_model}' not found: {clean_msg}",
                        provider="groq"
                    )

                if status_code in (500, 502, 503, 504):
                    raise ProviderUnavailableError(
                        f"Groq service unavailable ({status_code}): {clean_msg}",
                        provider="groq"
                    )

                raise LLMError(f"Groq API error ({status_code}, type={err_type}, code={err_code}): {clean_msg}", provider="groq")

        if last_error:
            raise last_error
        raise ProviderUnavailableError("All Groq candidate models failed.", provider="groq")
