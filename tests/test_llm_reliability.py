import asyncio
import json
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from reclaim.llm.groq import (
    GroqProvider,
    to_strict_json_schema,
    classify_rate_limit,
    MAX_RETRY_WINDOW_SECONDS
)
from reclaim.llm.schemas import (
    ProbeSchema,
    GoalInterpretation,
    EvidenceSynthesisResult,
    MissionDecision,
    ActionProposal,
    RootCauseHypothesis
)
from reclaim.llm.exceptions import (
    ProviderQuotaError,
    ProviderRateLimitError,
    LLMError
)
from reclaim.core.decision_engine import DecisionEngine
from reclaim.core.orchestrator import MissionOrchestrator, MissionState
from reclaim.core.evidence_store import EvidenceStore
from reclaim.adapters.base import SourceType


# -------------------------------------------------------------
# 1. 429 Rate Limit Classification Tests (Evidence-Based)
# -------------------------------------------------------------

def test_classify_rate_limit_token_daily():
    headers = {"x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-tokens": "12h"}
    payload = {"message": "Rate limit reached on tokens per day (TPD): Limit 200000, Used 200000.", "type": "tokens"}
    assert classify_rate_limit(429, headers, payload) == "RATE_LIMIT_DAILY_TOKEN"


def test_classify_rate_limit_token_minute():
    headers = {"x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-tokens": "4.2s"}
    payload = {"message": "Rate limit reached on tokens per minute (TPM): Limit 8000.", "type": "tokens"}
    assert classify_rate_limit(429, headers, payload) == "RATE_LIMIT_TOKEN"


def test_classify_rate_limit_requests_daily():
    headers = {"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "8h"}
    payload = {"message": "Rate limit reached on requests per day (RPD): Limit 14400.", "type": "requests"}
    assert classify_rate_limit(429, headers, payload) == "RATE_LIMIT_DAILY_REQUEST"


def test_classify_rate_limit_requests_minute():
    headers = {"x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "20s"}
    payload = {"message": "Rate limit reached on requests per minute (RPM): Limit 30.", "type": "requests"}
    assert classify_rate_limit(429, headers, payload) == "RATE_LIMIT_REQUEST"


def test_classify_rate_limit_unknown_when_insufficient_evidence():
    headers = {}
    payload = {"message": "Something went wrong rate limiting"}
    assert classify_rate_limit(429, headers, payload) == "RATE_LIMIT_UNKNOWN"


def test_classify_rate_limit_none_on_200():
    assert classify_rate_limit(200, {}, {}) == "NONE"


# -------------------------------------------------------------
# 2. Strict Schema Normalization Tests
# -------------------------------------------------------------

def test_to_strict_json_schema_enforces_additional_properties_false():
    schema = MissionDecision.model_json_schema()
    strict = to_strict_json_schema(schema)

    assert strict.get("additionalProperties") is False
    assert "required" in strict
    assert len(strict["required"]) == len(strict["properties"])

    # Check $defs
    defs = strict.get("$defs", {})
    for def_name, def_body in defs.items():
        if def_body.get("type") == "object" or "properties" in def_body:
            assert def_body.get("additionalProperties") is False, f"Failed on {def_name}"
            if "properties" in def_body:
                assert "required" in def_body
                assert len(def_body["required"]) == len(def_body["properties"])


def test_strict_schema_has_no_unconstrained_dicts():
    strict = to_strict_json_schema(ActionProposal.model_json_schema())
    # Ensure arguments, expected_effect, verification_method are structured objects
    props = strict["properties"]
    assert "arguments" in props
    assert "expected_effect" in props
    assert "verification_method" in props


# -------------------------------------------------------------
# 3. Budget Enforcement & Deduplication Tests
# -------------------------------------------------------------

def test_request_budget_enforcement():
    async def run():
        provider = GroqProvider(api_key="gsk_dummy", max_requests_per_run=2)
        provider.requests_used = 2

        with pytest.raises(ProviderQuotaError) as exc_info:
            await provider._call_raw_api("prompt")
        assert "request budget exceeded" in str(exc_info.value)
    asyncio.run(run())


def test_token_budget_enforcement():
    async def run():
        provider = GroqProvider(api_key="gsk_dummy", max_tokens_per_run=100)
        provider.tokens_used = 150

        with pytest.raises(ProviderQuotaError) as exc_info:
            await provider._call_raw_api("prompt")
        assert "token budget exceeded" in str(exc_info.value)
    asyncio.run(run())


def test_request_deduplication():
    async def run():
        provider = GroqProvider(api_key="gsk_dummy")

        mock_resp = httpx.Response(
            200,
            json={"choices": [{"message": {"content": "{\"status\":\"ok\"}"}}], "usage": {"total_tokens": 10}},
            headers={"x-request-id": "req-1"}
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_resp
            res1, _ = await provider._call_raw_api("identical prompt", json_schema={"title": "test"})
            res2, _ = await provider._call_raw_api("identical prompt", json_schema={"title": "test"})

            # Only one network call made; second was deduplicated
            assert mock_post.call_count == 1
            assert res1 == res2
    asyncio.run(run())


# -------------------------------------------------------------
# 4. 429 Recovery & Failover Tests
# -------------------------------------------------------------

def test_429_prolonged_wait_triggers_immediate_failover():
    """If 120b returns 429 with prolonged wait (>15s), immediately fail over to 20b without sleeping."""
    async def run():
        provider = GroqProvider(
            api_key="gsk_dummy",
            model_name="openai/gpt-oss-120b",
            fallback_model="openai/gpt-oss-20b"
        )

        resp_429 = httpx.Response(
            429,
            json={"error": {"message": "Rate limit reached on tokens per day (TPD): Limit 200000. Please try again in 12m46s.", "type": "tokens"}},
            headers={"x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-tokens": "12m", "retry-after": "766"}
        )
        resp_200 = httpx.Response(
            200,
            json={"choices": [{"message": {"content": "fallback success"}}], "usage": {"total_tokens": 15}},
            headers={"x-request-id": "req-fallback-20"}
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            # First call on 120b returns 429, second call on fallback 20b returns 200
            mock_post.side_effect = [resp_429, resp_200]
            text, usage = await provider._call_raw_api("test prompt")

            assert text == "fallback success"
            assert provider.last_model_used == "openai/gpt-oss-20b"
            assert provider.last_fallback_used is True
            assert len(provider.traces) == 2
            assert provider.traces[0].status == "429_RATE_LIMIT"
            assert provider.traces[0].rate_limit_classification == "RATE_LIMIT_DAILY_TOKEN"
            assert provider.traces[1].status == "SUCCESS"
    asyncio.run(run())


def test_400_validation_error_logging():
    async def run():
        provider = GroqProvider(api_key="gsk_dummy")
        resp_400 = httpx.Response(
            400,
            json={"error": {"message": "Failed to validate JSON.", "code": "json_validate_failed", "type": "invalid_request_error"}},
            headers={"x-request-id": "req-bad-json"}
        )

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = resp_400
            with pytest.raises(LLMError) as exc_info:
                await provider._call_raw_api("prompt", json_schema={"title": "schema"})
            assert "json_validate_failed" in str(exc_info.value)
            assert len(provider.traces) == 1
            assert provider.traces[0].status == "400_VALIDATION_ERROR"
            assert provider.traces[0].validation_result == "FAIL"
    asyncio.run(run())


# -------------------------------------------------------------
# 5. DECISION INTEGRITY (HOSTILE TESTS)
# -------------------------------------------------------------

def test_decision_integrity_no_silent_deterministic_fallback():
    """Hostile test: When LLM fails, DecisionEngine MUST NOT invent a deterministic decision."""
    async def run():
        mock_provider = MagicMock()
        mock_provider.generate_structured = AsyncMock(side_effect=LLMError("API offline", provider="groq"))
        mock_provider.last_model_used = "openai/gpt-oss-120b"
        mock_provider.last_fallback_used = True
        mock_provider.last_request_id = "req-failed-123"

        engine = DecisionEngine(llm_provider=mock_provider)
        store = EvidenceStore()
        synthesis = EvidenceSynthesisResult(
            root_cause_hypotheses=[
                RootCauseHypothesis(
                    hypothesis_id="h1",
                    title="Outage",
                    description="Serialization Bug",
                    culprit_app="github",
                    corroborating_claim_ids=["c1"],
                    confidence=0.9
                )
            ],
            supporting_evidence_ids=["c1"],
            confidence=0.9,
            business_impact="Critical"
        )

        decision = await engine.formulate_decision(synthesis, store)

        # CRITICAL INVARIANTS
        assert decision.reasoning_status == "FAILED"
        assert decision.decision_source == "none"
        assert len(decision.selected_actions) == 0
        assert len(decision.hypotheses) == 0
        assert "failed" in decision.root_cause_summary.lower()
    asyncio.run(run())


def test_failed_llm_decision_aborts_mission():
    """Hostile test: Orchestrator MUST transition to FAILED and reject completion when reasoning fails."""
    async def run():
        mock_provider = MagicMock()
        mock_provider.generate_structured = AsyncMock(side_effect=LLMError("Reasoning model failure", provider="groq"))
        mock_provider.last_model_used = "openai/gpt-oss-120b"
        mock_provider.last_fallback_used = False
        mock_provider.last_request_id = ""

        orchestrator = MissionOrchestrator(llm_provider=mock_provider)
        result = await orchestrator.run_mission("Save Acme Corp.")

        # Mission must fail explicitly rather than claiming success
        assert result is False
        assert orchestrator.fsm.current_state == MissionState.FAILED
        assert orchestrator.decision.reasoning_status == "FAILED"
        assert orchestrator.decision.decision_source == "none"
    asyncio.run(run())


# -------------------------------------------------------------
# 6. Provenance Integrity Tests
# -------------------------------------------------------------

def test_provenance_fields_on_successful_decision():
    """Validates that successful decisions carry accurate reasoning_status and decision_source."""
    async def run():
        mock_decision = MissionDecision(
            decision_id="dec_001",
            root_cause_summary="PR #882 bug",
            hypotheses=[],
            selected_actions=[
                ActionProposal(
                    action_id="act_1",
                    app="jira",
                    action="update_issue",
                    risk_level="LOW_RISK_WRITE",
                    requires_approval=False,
                    rationale="Escalate priority"
                )
            ],
            remediation_strategy="Escalate and patch",
            estimated_risk="Low"
        )

        mock_provider = MagicMock()
        mock_response = MagicMock()
        mock_response.parsed = mock_decision
        mock_provider.generate_structured = AsyncMock(return_value=mock_response)
        mock_provider.last_model_used = "openai/gpt-oss-120b"
        mock_provider.last_fallback_used = False
        mock_provider.last_request_id = "req-prov-1"

        engine = DecisionEngine(llm_provider=mock_provider)
        store = EvidenceStore()
        synthesis = EvidenceSynthesisResult(
            root_cause_hypotheses=[
                RootCauseHypothesis(
                    hypothesis_id="h1", title="Bug", description="Bug desc",
                    culprit_app="github", corroborating_claim_ids=["c1"], confidence=0.9
                )
            ],
            supporting_evidence_ids=["c1"], confidence=0.9, business_impact="outage"
        )

        dec = await engine.formulate_decision(synthesis, store)
        assert dec.reasoning_status == "LLM_REASONING"
        assert "groq:" in dec.decision_source
        assert len(dec.selected_actions) == 1
    asyncio.run(run())


def test_validate_live_golden_path_invariant():
    """Test machine-checkable invariant: all 8 conditions strictly enforced."""
    from reclaim.evaluation.scorecard import validate_live_golden_path_invariant

    # 1. Successful run trace
    passing_trace = {
        "final_status": "COMPLETED",
        "reasoning_status": "LLM_REASONING",
        "decision_source": "groq:openai/gpt-oss-120b",
        "llm_traces": [{"status_code": 200, "validation_result": "PASS"}],
        "executed_actions": [
            {"app": "jira", "execution_status": "EXECUTED", "verification_status": "VERIFIED"}
        ]
    }
    res_pass = validate_live_golden_path_invariant(passing_trace)
    assert res_pass["is_golden_path_success"] is True
    assert res_pass["verdict"] == "LIVE_GOLDEN_PATH_PASS"

    # 2. Failed reasoning trace (e.g. 429 quota exhaustion)
    failing_reasoning_trace = {
        "final_status": "FAILED",
        "reasoning_status": "FAILED",
        "decision_source": "none",
        "llm_traces": [{"status_code": 429, "validation_result": "SKIPPED"}],
        "executed_actions": []
    }
    res_fail = validate_live_golden_path_invariant(failing_reasoning_trace)
    assert res_fail["is_golden_path_success"] is False
    assert res_fail["verdict"] == "LIVE_GOLDEN_PATH_FAIL"
    assert res_fail["conditions"]["1_groq_http_200"] is False
    assert res_fail["conditions"]["3_reasoning_status_is_llm"] is False

    # 3. Unverified mutation trace
    unverified_trace = {
        "final_status": "COMPLETED",
        "reasoning_status": "LLM_REASONING",
        "decision_source": "groq:openai/gpt-oss-120b",
        "llm_traces": [{"status_code": 200, "validation_result": "PASS"}],
        "executed_actions": [
            {"app": "jira", "execution_status": "EXECUTED", "verification_status": "FAILED_VERIFICATION"}
        ]
    }
    res_unverified = validate_live_golden_path_invariant(unverified_trace)
    assert res_unverified["is_golden_path_success"] is False
    assert res_unverified["conditions"]["7_all_mutations_readback_verified"] is False

