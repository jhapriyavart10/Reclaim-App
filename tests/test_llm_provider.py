import os
import json
import asyncio
import pytest
import httpx
from unittest.mock import AsyncMock, patch

from reclaim.config import Settings, sanitize_secret
from reclaim.llm import (
    LLMProvider,
    get_llm_provider,
    AgentPlan,
    MissionDecision,
    ActionProposal,
    ProbeSchema,
    ProviderAuthError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderUnavailableError,
    SchemaValidationError,
    ProviderConfigurationError,
)
from reclaim.llm.gemini import GeminiProvider
from reclaim.llm.groq import GroqProvider
from reclaim.llm.mock import MockProvider


# ============================================================================
# 1. Gemini Successful Request
# ============================================================================
def test_gemini_successful_request():
    async def run():
        provider = GeminiProvider(api_key="AIzaSyDummyKeyForTestingOnly", model_name="gemini-2.0-flash")

        mock_gemini_response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "text": json.dumps({
                                    "status": "ok",
                                    "provider_name": "gemini",
                                    "latency_probe_id": "probe-gemini-101"
                                })
                            }
                        ]
                    },
                    "finishReason": "STOP"
                }
            ],
            "usageMetadata": {
                "promptTokenCount": 25,
                "candidatesTokenCount": 15,
                "totalTokenCount": 40
            }
        }

        mock_response = httpx.Response(200, json=mock_gemini_response, request=httpx.Request("POST", "https://api.test"))

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response
            res = await provider.generate_structured(ProbeSchema, prompt="Test probe")

            assert res.parsed.status == "ok"
            assert res.parsed.provider_name == "gemini"
            assert res.usage.total_tokens == 40
            assert res.provider == "gemini"

    asyncio.run(run())


# ============================================================================
# 2. Gemini Quota/Rate-limit -> Automatic Fallback to Groq in AUTO Mode
# ============================================================================
def test_gemini_quota_fallback_to_groq_in_auto_mode():
    async def run():
        cfg = Settings(
            LLM_PROVIDER="auto",
            GEMINI_API_KEY="AIzaSyTestKey",
            GROQ_API_KEY="gsk_TestGroqKey"
        )

        gemini_error_resp = httpx.Response(
            429,
            json={"error": {"message": "Resource has been exhausted (e.g. check quota).", "status": "RESOURCE_EXHAUSTED"}},
            request=httpx.Request("POST", "https://api.test")
        )

        groq_success_resp = httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"status": "ok", "provider_name": "groq", "latency_probe_id": "probe-01"})}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20}
            },
            request=httpx.Request("POST", "https://api.test")
        )

        async def side_effect(url, **kwargs):
            if "generativelanguage" in str(url):
                return gemini_error_resp
            return groq_success_resp

        with patch("httpx.AsyncClient.post", side_effect=side_effect):
            provider = await get_llm_provider(custom_settings=cfg)
            assert isinstance(provider, GroqProvider)
            assert provider.provider_name == "groq"

    asyncio.run(run())


# ============================================================================
# 3. Gemini Malformed Structured Output -> Bounded Retry
# ============================================================================
def test_gemini_malformed_structured_output_bounded_retry():
    async def run():
        provider = GeminiProvider(api_key="AIzaSyTestKey")

        bad_resp = httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": "THIS IS NOT VALID JSON"}]}}]},
            request=httpx.Request("POST", "https://api.test")
        )

        good_resp = httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [{
                                "text": json.dumps({"status": "ok", "provider_name": "gemini", "latency_probe_id": "retry-success"})
                            }]
                        }
                    }
                ],
                "usageMetadata": {"totalTokenCount": 50}
            },
            request=httpx.Request("POST", "https://api.test")
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.side_effect = [bad_resp, good_resp]
            res = await provider.generate_structured(ProbeSchema, prompt="Probe me", max_retries=2)
            assert res.parsed.latency_probe_id == "retry-success"
            assert mock_post.call_count == 2

    asyncio.run(run())


# ============================================================================
# 4. Groq Successful Request
# ============================================================================
def test_groq_successful_request():
    async def run():
        provider = GroqProvider(api_key="gsk_TestKeyForTesting", model_name="openai/gpt-oss-120b")

        mock_groq_payload = {
            "choices": [
                {
                    "message": {
                        "content": json.dumps({
                            "status": "ready",
                            "provider_name": "groq",
                            "latency_probe_id": "groq-fast-01"
                        })
                    }
                }
            ],
            "usage": {
                "prompt_tokens": 30,
                "completion_tokens": 12,
                "total_tokens": 42
            }
        }

        mock_resp = httpx.Response(200, json=mock_groq_payload, request=httpx.Request("POST", "https://api.test"))

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            res = await provider.generate_structured(ProbeSchema, prompt="Test Groq")
            assert res.parsed.status == "ready"
            assert res.parsed.provider_name == "groq"
            assert res.usage.total_tokens == 42
            assert res.provider == "groq"

    asyncio.run(run())


# ============================================================================
# 5. Both Providers Unavailable -> Clear Failure
# ============================================================================
def test_both_providers_unavailable_clear_failure():
    async def run():
        cfg = Settings(
            LLM_PROVIDER="auto",
            GEMINI_API_KEY="AIzaSyTestKey",
            GROQ_API_KEY="gsk_TestGroqKey"
        )

        err_resp = httpx.Response(
            503,
            json={"error": {"message": "Service Unavailable"}},
            request=httpx.Request("POST", "https://api.test")
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = err_resp
            with pytest.raises(ProviderConfigurationError) as exc_info:
                await get_llm_provider(custom_settings=cfg)

            msg = str(exc_info.value)
            assert "Neither Gemini nor Groq is currently usable in AUTO mode" in msg
            assert "GEMINI_API_KEY" in msg
            assert "GROQ_API_KEY" in msg

    asyncio.run(run())


# ============================================================================
# 6. Explicit GEMINI Mode Never Silently Switches to Groq
# ============================================================================
def test_explicit_gemini_mode_never_switches_to_groq():
    async def run():
        cfg = Settings(
            LLM_PROVIDER="gemini",
            GEMINI_API_KEY="AIzaSyTestKey",
            GROQ_API_KEY="gsk_TestGroqKey"
        )

        provider = await get_llm_provider(custom_settings=cfg)
        assert isinstance(provider, GeminiProvider)
        assert provider.provider_name == "gemini"

        err_resp = httpx.Response(
            429,
            json={"error": {"message": "Resource exhausted quota", "status": "RESOURCE_EXHAUSTED"}},
            request=httpx.Request("POST", "https://api.test")
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = err_resp
            with pytest.raises(ProviderQuotaError) as exc_info:
                await provider.generate_raw("Test query")
            assert exc_info.value.provider == "gemini"

    asyncio.run(run())


# ============================================================================
# 7. Explicit GROQ Mode Never Calls Gemini
# ============================================================================
def test_explicit_groq_mode_never_calls_gemini():
    async def run():
        cfg = Settings(
            LLM_PROVIDER="groq",
            GEMINI_API_KEY="AIzaSyTestKey",
            GROQ_API_KEY="gsk_TestGroqKey"
        )

        with patch("reclaim.llm.gemini.GeminiProvider") as mock_gemini_cls:
            provider = await get_llm_provider(custom_settings=cfg)
            assert isinstance(provider, GroqProvider)
            assert provider.provider_name == "groq"
            mock_gemini_cls.assert_not_called()

    asyncio.run(run())


# ============================================================================
# 8. Mock Provider Works Without Network Access
# ============================================================================
def test_mock_provider_works_without_network():
    async def run():
        cfg = Settings(LLM_PROVIDER="mock")
        provider = await get_llm_provider(custom_settings=cfg)

        assert isinstance(provider, MockProvider)
        assert provider.provider_name == "mock"

        assert await provider.test_probe() is True

        plan_resp = await provider.generate_plan(goal="Save Acme Corp.", context={"arr": 250000})
        assert isinstance(plan_resp.parsed, AgentPlan)
        assert len(plan_resp.parsed.investigation_steps) >= 5

        decision_resp = await provider.generate_decision(goal="Save Acme Corp.", evidence=[])
        assert isinstance(decision_resp.parsed, MissionDecision)
        assert len(decision_resp.parsed.selected_actions) >= 4

        report_resp = await provider.generate_final_report(
            goal="Save Acme Corp.",
            decision=decision_resp.parsed,
            verification_results=[]
        )
        assert "# Mission Post-Mortem" in report_resp.parsed

    asyncio.run(run())


# ============================================================================
# 9. Invalid LLM-Generated ActionProposal Cannot Reach Executor
# ============================================================================
def test_invalid_llm_action_proposal_cannot_reach_executor():
    async def run():
        provider = GeminiProvider(api_key="AIzaSyTestKey")

        invalid_proposal_json = json.dumps({
            "action_id": "act_corrupted_999",
            "app": "jira"
        })

        bad_resp = httpx.Response(
            200,
            json={"candidates": [{"content": {"parts": [{"text": invalid_proposal_json}]}}]},
            request=httpx.Request("POST", "https://api.test")
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = bad_resp
            with pytest.raises(SchemaValidationError) as exc_info:
                await provider.generate_structured(ActionProposal, prompt="Make action", max_retries=1)

            assert "Failed to generate valid structured output for ActionProposal" in str(exc_info.value)

    asyncio.run(run())


# ============================================================================
# 10. Provider API Keys Never Appear in Logs or Sanitized Messages
# ============================================================================
def test_provider_api_keys_never_appear_in_logs():
    raw_gemini_key = "AIzaSyB3498fkdfjhskjfdshfkjshf8324"
    raw_groq_key = "gsk_983274982374982374983274983274"

    masked_gemini = sanitize_secret(raw_gemini_key)
    masked_groq = sanitize_secret(raw_groq_key)

    assert raw_gemini_key not in masked_gemini
    assert raw_groq_key not in masked_groq
    assert masked_gemini.startswith("AIza...")
    assert masked_groq.startswith("gsk_...")

    gemini_provider = GeminiProvider(api_key=raw_gemini_key)
    leak_error_resp = httpx.Response(
        400,
        text=f"API key not valid. Please pass a valid key: {raw_gemini_key}",
        request=httpx.Request("POST", "https://api.test")
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = leak_error_resp
        with pytest.raises(Exception) as exc_info:
            asyncio.run(gemini_provider.test_probe())

        err_text = str(exc_info.value)
        assert raw_gemini_key not in err_text
        assert masked_gemini in err_text


# ============================================================================
# Live Integration Hooks
# ============================================================================
def test_live_gemini_integration():
    """Live smoke test against Google Gemini API (skipped if key missing)."""
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        pytest.skip("SKIPPED — missing credentials (GEMINI_API_KEY / GOOGLE_API_KEY)")

    async def run():
        provider = GeminiProvider(api_key=key, model_name=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"))
        probe_ok = await provider.test_probe()
        assert probe_ok is True

    asyncio.run(run())


def test_live_groq_integration():
    """Live smoke test against Groq completions API."""
    key = os.getenv("GROQ_API_KEY")
    if not key:
        pytest.skip("SKIPPED — missing credentials (GROQ_API_KEY)")

    async def run():
        model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
        provider = GroqProvider(api_key=key, model_name=model)
        try:
            probe_ok = await provider.test_probe()
            assert probe_ok is True
        except ProviderRateLimitError as e:
            pytest.skip(f"Live Groq daily quota / rate limit reached: {e}")

    asyncio.run(run())


def test_live_groq_structured_pydantic_generation():
    """Live test verifying Groq generates valid structured Pydantic AgentPlan."""
    key = os.getenv("GROQ_API_KEY")
    if not key:
        pytest.skip("SKIPPED — missing credentials (GROQ_API_KEY)")

    async def run():
        model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
        provider = GroqProvider(api_key=key, model_name=model)
        try:
            resp = await provider.generate_plan(
                goal="Save Acme Corp.",
                context={"customer": "Acme Corp", "arr": 250000, "status": "churn_risk"}
            )
            assert isinstance(resp.parsed, AgentPlan)
            assert resp.parsed.goal == "Save Acme Corp."
            assert len(resp.parsed.investigation_steps) >= 1
            assert resp.usage.total_tokens > 0
        except ProviderRateLimitError as e:
            pytest.skip(f"Live Groq daily quota / rate limit reached: {e}")

    asyncio.run(run())


def test_live_groq_decommissioned_model_classification():
    """Live test verifying Groq classifies decommissioned model as ProviderUnavailableError."""
    key = os.getenv("GROQ_API_KEY")
    if not key:
        pytest.skip("SKIPPED — missing credentials (GROQ_API_KEY)")

    async def run():
        # Specifically test an obsolete model ID
        obsolete_provider = GroqProvider(api_key=key, model_name="llama-3.3-70b-versatile")
        with pytest.raises(ProviderUnavailableError) as exc_info:
            await obsolete_provider.test_probe()
        assert "not found or decommissioned" in str(exc_info.value) or "does not exist" in str(exc_info.value)

    asyncio.run(run())

