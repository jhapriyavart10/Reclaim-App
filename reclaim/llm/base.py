import abc
import json
import logging
import re
import time
from typing import Type, TypeVar, Optional, List, Dict, Any, Generic
from pydantic import BaseModel, ValidationError

from reclaim.llm.exceptions import (
    LLMError,
    SchemaValidationError,
    ProviderUnavailableError
)
from reclaim.llm.schemas import (
    AgentPlan,
    MissionDecision,
    EvidenceClaim,
    VerificationResult,
    ProbeSchema
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class UsageStats(BaseModel):
    """Tracks token and latency consumption for cost and rate-limit awareness."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0


class LLMResponse(Generic[T]):
    """Standardized response container returned by all providers."""
    def __init__(
        self,
        parsed: T,
        raw_text: str,
        usage: UsageStats,
        provider: str,
        model: str,
        request_id: str = "",
        reasoning_status: str = "LLM_REASONING",
        decision_source: str = "",
        fallback_used: bool = False,
        validation_result: str = "PASS"
    ):
        self.parsed = parsed
        self.raw_text = raw_text
        self.usage = usage
        self.provider = provider
        self.model = model
        self.request_id = request_id
        self.reasoning_status = reasoning_status
        self.decision_source = decision_source or f"{provider}:{model}"
        self.fallback_used = fallback_used
        self.validation_result = validation_result

    def __repr__(self) -> str:
        return f"<LLMResponse provider={self.provider} model={self.model} latency={self.usage.latency_ms:.1f}ms status={self.reasoning_status}>"


class LLMProvider(abc.ABC):
    """
    Abstract Base Class for all RECLAIM LLM providers.
    Enforces unified interfaces, bounded retries, schema validation, and token tracking.
    """

    def __init__(self, model_name: str, timeout_seconds: float = 25.0):
        self.model_name = model_name
        self.timeout_seconds = timeout_seconds
        self.last_request_id: str = ""
        self.last_model_used: str = model_name
        self.last_fallback_used: bool = False
        self.last_rate_limit_class: str = "NONE"

    @property
    @abc.abstractmethod
    def provider_name(self) -> str:
        """Name of the provider (e.g., 'gemini', 'groq', 'mock')."""
        pass

    @abc.abstractmethod
    async def _call_raw_api(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        json_schema: Optional[Dict[str, Any]] = None
    ) -> tuple[str, UsageStats]:
        """Low-level provider call returning (raw_text_response, usage_stats)."""
        pass

    async def generate_raw(
        self,
        prompt: str,
        system_prompt: Optional[str] = None
    ) -> LLMResponse[str]:
        """Generates raw text response."""
        start_time = time.perf_counter()
        raw_text, usage = await self._call_raw_api(prompt, system_prompt=system_prompt)
        usage.latency_ms = (time.perf_counter() - start_time) * 1000.0
        active_model = getattr(self, "last_model_used", self.model_name)
        request_id = getattr(self, "last_request_id", "")
        fallback_used = getattr(self, "last_fallback_used", False)
        return LLMResponse(
            parsed=raw_text,
            raw_text=raw_text,
            usage=usage,
            provider=self.provider_name,
            model=active_model,
            request_id=request_id,
            reasoning_status="LLM_REASONING",
            decision_source=f"{self.provider_name}:{active_model}",
            fallback_used=fallback_used,
            validation_result="PASS"
        )

    async def generate_structured(
        self,
        schema: Type[T],
        prompt: str,
        system_prompt: Optional[str] = None,
        max_retries: int = 2
    ) -> LLMResponse[T]:
        """
        Generates structured output guaranteed to validate against the provided Pydantic schema.
        Includes bounded retries: if parsing or validation fails, retries with the error feedback.
        Invalid output is logged and never returned.
        """
        current_prompt = prompt
        schema_dict = schema.model_json_schema()
        schema_json_str = json.dumps(schema_dict, indent=2)

        augmented_system_prompt = (
            (system_prompt or "You are a precise, mission-critical autonomous enterprise AI agent.")
            + "\n\nYou MUST respond with a single valid JSON object strictly conforming to this JSON Schema:\n"
            + schema_json_str
            + "\nOutput ONLY valid JSON without markdown fences, explanation, or preamble."
        )

        attempts = 0
        last_error: Optional[Exception] = None
        last_raw_text = ""

        while attempts <= max_retries:
            attempts += 1
            start_time = time.perf_counter()
            try:
                raw_text, usage = await self._call_raw_api(
                    prompt=current_prompt,
                    system_prompt=augmented_system_prompt,
                    json_schema=schema_dict
                )
                usage.latency_ms = (time.perf_counter() - start_time) * 1000.0
                last_raw_text = raw_text

                # Clean potential markdown fences (e.g. ```json ... ```)
                cleaned_text = raw_text.strip()
                if cleaned_text.startswith("```"):
                    cleaned_text = re.sub(r"^```[a-zA-Z]*\n?", "", cleaned_text)
                    cleaned_text = re.sub(r"\n?```$", "", cleaned_text).strip()

                parsed_json = json.loads(cleaned_text)
                validated_obj = schema.model_validate(parsed_json)

                active_model = getattr(self, "last_model_used", self.model_name)
                request_id = getattr(self, "last_request_id", "")
                fallback_used = getattr(self, "last_fallback_used", False)
                decision_source = f"{self.provider_name}:{active_model}"

                # Ensure decision provenance fields are populated on validated objects
                if hasattr(validated_obj, "reasoning_status"):
                    setattr(validated_obj, "reasoning_status", "LLM_REASONING")
                if hasattr(validated_obj, "decision_source"):
                    setattr(validated_obj, "decision_source", decision_source)
                if hasattr(validated_obj, "model"):
                    setattr(validated_obj, "model", active_model)
                if hasattr(validated_obj, "request_id"):
                    setattr(validated_obj, "request_id", request_id)
                if hasattr(validated_obj, "fallback_used"):
                    setattr(validated_obj, "fallback_used", fallback_used)

                return LLMResponse(
                    parsed=validated_obj,
                    raw_text=raw_text,
                    usage=usage,
                    provider=self.provider_name,
                    model=active_model,
                    request_id=request_id,
                    reasoning_status="LLM_REASONING",
                    decision_source=decision_source,
                    fallback_used=fallback_used,
                    validation_result="PASS"
                )

            except (json.JSONDecodeError, ValidationError) as err:
                last_error = err
                logger.warning(
                    f"[{self.provider_name.upper()}] Structured validation failed on attempt {attempts}/{max_retries + 1}: {err}"
                )
                if attempts <= max_retries:
                    # Provide targeted feedback for correction
                    current_prompt = (
                        f"{prompt}\n\n"
                        f"[SYSTEM CORRECTION ATTEMPT {attempts}]: Your previous output failed schema validation:\n"
                        f"{str(err)}\n\n"
                        f"Previous invalid output was:\n{last_raw_text[:500]}\n\n"
                        f"Please output ONLY a valid JSON object strictly matching the required schema."
                    )
                else:
                    break

        raise SchemaValidationError(
            f"Failed to generate valid structured output for {schema.__name__} after {max_retries + 1} attempts: {last_error}",
            provider=self.provider_name,
            raw_text=last_raw_text,
            raw_error=last_error
        )

    async def test_probe(self) -> bool:
        """Fast minimal probe to test provider viability."""
        try:
            res = await self.generate_structured(
                schema=ProbeSchema,
                prompt="Respond with status ok, your provider name, and probe id 'probe-001'.",
                max_retries=1
            )
            return res.parsed.status in ["ok", "ready"]
        except Exception as e:
            logger.warning(f"Provider {self.provider_name} probe failed: {e}")
            raise

    # -------------------------------------------------------------
    # High-Level Agent Domain Methods (Provider Agnostic)
    # -------------------------------------------------------------

    async def generate_plan(self, goal: str, context: Dict[str, Any]) -> LLMResponse[AgentPlan]:
        """Generate the initial structured investigative plan across the 6 apps."""
        prompt = (
            f"GOAL: {goal}\n\n"
            f"CURRENT BUSINESS CONTEXT:\n{json.dumps(context, indent=2)}\n\n"
            "Formulate an initial investigation plan across CRM, Gmail, Slack, Jira, GitHub, and Calendar. "
            "Identify the target account, hypothesize initial churn factors, and specify targeted search queries."
        )
        return await self.generate_structured(schema=AgentPlan, prompt=prompt)

    async def generate_decision(
        self,
        goal: str,
        evidence: List[EvidenceClaim]
    ) -> LLMResponse[MissionDecision]:
        """Synthesize collected multi-app evidence into root causes and prioritized actions."""
        serialized_evidence = [e.model_dump() for e in evidence]
        prompt = (
            f"MISSION GOAL: {goal}\n\n"
            f"COLLECTED EVIDENCE ACROSS APPS:\n{json.dumps(serialized_evidence, indent=2)}\n\n"
            "Synthesize this evidence into root causes and determine necessary remediation actions. "
            "CRITICAL INVARIANTS:\n"
            "- Corroborate root causes across at least 2 distinct apps.\n"
            "- Low-risk internal actions (Jira escalate, internal Slack alert) have risk_level='LOW_RISK_WRITE'.\n"
            "- Customer-facing actions (direct customer email, booking customer calendar) MUST have risk_level='HIGH_RISK_WRITE' and requires_approval=True.\n"
            "- Specify clear expected_effect and verification_method for every action."
        )
        return await self.generate_structured(schema=MissionDecision, prompt=prompt)

    async def generate_reasoning_summary(self, decision: MissionDecision) -> LLMResponse[str]:
        """Generate a concise, human-readable summary of the agent's reasoning."""
        prompt = (
            f"Summarize the following mission decision in 2-3 crisp executive sentences:\n"
            f"Root cause: {decision.root_cause_summary}\n"
            f"Strategy: {decision.remediation_strategy}\n"
            f"Number of actions: {len(decision.selected_actions)}"
        )
        return await self.generate_raw(prompt=prompt)

    async def generate_final_report(
        self,
        goal: str,
        decision: MissionDecision,
        verification_results: List[VerificationResult]
    ) -> LLMResponse[str]:
        """Generate the comprehensive, auditable mission report with verified proofs."""
        prompt = (
            f"Generate an auditable, executive-ready Business Rescue Mission Report.\n"
            f"MISSION GOAL: {goal}\n"
            f"ROOT CAUSE: {decision.root_cause_summary}\n"
            f"ACTIONS PLANNED: {len(decision.selected_actions)}\n"
            f"VERIFICATION PROOFS:\n"
            f"{json.dumps([v.model_dump() for v in verification_results], indent=2)}\n\n"
            "Format in professional Markdown with sections: Executive Summary, Cross-App Root Cause Analysis, Actions Executed & Verified, and Risk Status."
        )
        return await self.generate_raw(prompt=prompt)
