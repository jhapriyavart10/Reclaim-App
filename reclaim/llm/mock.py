import json
import logging
from typing import Optional, Dict, Any, Type, TypeVar
from pydantic import BaseModel

from reclaim.llm.base import LLMProvider, UsageStats, LLMResponse
from reclaim.llm.schemas import (
    AgentPlan,
    InvestigationStep,
    MissionDecision,
    RootCauseHypothesis,
    ActionProposal,
    ProbeSchema,
    EvidenceClaim,
    VerificationResult
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class MockProvider(LLMProvider):
    """
    Deterministic mock provider for offline development, integration testing,
    and the automated evaluation harness without consuming API quota.
    """

    def __init__(self, model_name: str = "mock-deterministic-v1", timeout_seconds: float = 5.0):
        super().__init__(model_name=model_name, timeout_seconds=timeout_seconds)
        self.scenario: str = "golden_path"
        self.call_count: int = 0

    @property
    def provider_name(self) -> str:
        return "mock"

    async def _call_raw_api(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        json_schema: Optional[Dict[str, Any]] = None
    ) -> tuple[str, UsageStats]:
        self.call_count += 1
        usage = UsageStats(prompt_tokens=42, completion_tokens=84, total_tokens=126, latency_ms=1.5)
        return '{"mock": true}', usage

    async def generate_raw(
        self,
        prompt: str,
        system_prompt: Optional[str] = None
    ) -> LLMResponse[str]:
        self.call_count += 1
        usage = UsageStats(prompt_tokens=50, completion_tokens=120, total_tokens=170, latency_ms=2.0)
        content = (
            "# Mission Post-Mortem & Rescue Report: Acme Corp\n\n"
            "## Executive Summary\n"
            "RECLAIM successfully intervened to prevent churn of Tier-1 Enterprise account Acme Corp ($250,000 ARR). "
            "The churn risk was driven by a silent breaking change introduced in PR #882.\n\n"
            "## Actions Executed & Independently Verified\n"
            "1. **Jira PROD-1042**: Escalated from Medium to P0 Blocker and assigned to Lead Engineer.\n"
            "2. **Internal Slack**: Emergency notification posted in #incident-war-room.\n"
            "3. **Customer Outreach (Approved)**: RCA email dispatched to Sarah Chen (CTO).\n"
            "4. **Calendar Sync (Approved)**: 15-minute Executive sync booked.\n\n"
            "## Verification Status\n"
            "All 4 mutations independently confirmed in destination application state stores."
        )
        return LLMResponse(
            parsed=content,
            raw_text=content,
            usage=usage,
            provider=self.provider_name,
            model=self.model_name
        )

    async def generate_structured(
        self,
        schema: Type[T],
        prompt: str,
        system_prompt: Optional[str] = None,
        max_retries: int = 2
    ) -> LLMResponse[T]:
        self.call_count += 1
        usage = UsageStats(prompt_tokens=100, completion_tokens=250, total_tokens=350, latency_ms=3.0)

        # Handle Probe
        if schema is ProbeSchema:
            obj = ProbeSchema(status="ok", provider_name="mock", latency_probe_id="mock-probe-01")
            return LLMResponse(parsed=obj, raw_text=obj.model_dump_json(), usage=usage, provider="mock", model=self.model_name)

        # Handle AgentPlan
        if schema is AgentPlan:
            plan = AgentPlan(
                goal="Save Acme Corp.",
                initial_assessment="Acme Corp is a high-value enterprise account ($250k ARR) facing churn. Multi-app investigation required to discover root cause.",
                investigation_steps=[
                    InvestigationStep(
                        step_id="inv_crm_01",
                        app="crm",
                        intent="Query account renewal status and health score trajectory",
                        query_params={"account_name": "Acme Corp"},
                        expected_information="Health score, renewal deadline, key executive contacts"
                    ),
                    InvestigationStep(
                        step_id="inv_gmail_01",
                        app="gmail",
                        intent="Search recent communications from customer executives",
                        query_params={"query": "from:sarah@acme.com", "limit": 5},
                        expected_information="Specific complaints, ultimatum notices, or sentiment"
                    ),
                    InvestigationStep(
                        step_id="inv_slack_01",
                        app="slack",
                        intent="Check internal customer escalation channels",
                        query_params={"channels": ["customer-acme", "alerts-enterprise"], "query": "Acme"},
                        expected_information="Internal engineering chatter regarding outages or bugs"
                    ),
                    InvestigationStep(
                        step_id="inv_jira_01",
                        app="jira",
                        intent="Identify unresolved blocking tickets or API failures",
                        query_params={"jql": 'text ~ "data-sync" OR text ~ "Acme"'},
                        expected_information="Bug severity, backlog priority, assigned engineers"
                    ),
                    InvestigationStep(
                        step_id="inv_github_01",
                        app="github",
                        intent="Inspect recent pull requests and commits related to data-sync",
                        query_params={"repo": "backend-core", "query": "data-sync serializer"},
                        expected_information="Breaking code changes or regressions"
                    )
                ],
                risk_summary="High churn risk if customer communication is delayed beyond today."
            )
            return LLMResponse(parsed=plan, raw_text=plan.model_dump_json(), usage=usage, provider="mock", model=self.model_name)

        # Handle MissionDecision
        if schema is MissionDecision:
            decision = MissionDecision(
                decision_id="dec_acme_01",
                root_cause_summary=(
                    "Acme Corp renewal is at imminent risk due to a production 500 error on `/v2/data-sync` "
                    "caused by PR #882 (strict schema serialization), which broke Acme's legacy integration. "
                    "Jira issue PROD-1042 was mistakenly triaged as Medium in the backlog."
                ),
                hypotheses=[
                    RootCauseHypothesis(
                        hypothesis_id="hyp_api_regression",
                        title="PR #882 Serialization Regression Breaking Acme API Sync",
                        description="Strict JSON schema update broke backward compatibility with Acme's endpoint format.",
                        culprit_app="github",
                        corroborating_claim_ids=["ev_gmail_01", "ev_slack_01", "ev_jira_01", "ev_gh_01"],
                        confidence=0.96
                    )
                ],
                selected_actions=[
                    ActionProposal(
                        action_id="act_escalate_jira",
                        app="jira",
                        action="update_issue",
                        arguments={
                            "issue_key": "PROD-1042",
                            "priority": "P0 - Blocker",
                            "assignee": "alex_tech_lead",
                            "comment": "Escalated to P0 Blocker by RECLAIM - root cause of Acme Corp churn risk."
                        },
                        risk_level="LOW_RISK_WRITE",
                        requires_approval=False,
                        rationale="Internal ticket escalation to mobilize engineering fix immediately.",
                        expected_effect={"issue_key": "PROD-1042", "priority": "P0 - Blocker", "assignee": "alex_tech_lead"},
                        verification_method={"type": "query_issue", "issue_key": "PROD-1042"}
                    ),
                    ActionProposal(
                        action_id="act_slack_war_room",
                        app="slack",
                        action="post_message",
                        arguments={
                            "channel": "incident-war-room",
                            "text": ":rotating_light: P0 Alert: Acme Corp churn risk identified. PROD-1042 escalated to Alex. Fix underway."
                        },
                        risk_level="LOW_RISK_WRITE",
                        requires_approval=False,
                        rationale="Notify engineering team of active high-priority remediation.",
                        expected_effect={"channel": "incident-war-room", "message_sent": True},
                        verification_method={"type": "query_channel_history", "channel": "incident-war-room"}
                    ),
                    ActionProposal(
                        action_id="act_email_sarah",
                        app="gmail",
                        action="send_email",
                        arguments={
                            "to": "sarah@acme.com",
                            "subject": "Update regarding API v2 data sync & executive resolution plan",
                            "body": "Dear Sarah, We identified the root cause of the /v2/data-sync errors in our v2.4.0 release. A hotfix is underway..."
                        },
                        risk_level="HIGH_RISK_WRITE",
                        requires_approval=True,
                        rationale="Customer-facing communication to de-escalate churn threat with executive sponsor.",
                        expected_effect={"to": "sarah@acme.com", "status": "sent"},
                        verification_method={"type": "query_sent_emails", "recipient": "sarah@acme.com"}
                    ),
                    ActionProposal(
                        action_id="act_calendar_sync",
                        app="calendar",
                        action="create_meeting",
                        arguments={
                            "summary": "Acme Corp / RECLAIM Incident Post-Mortem & Fix Review",
                            "attendees": ["sarah@acme.com", "lead_architect@company.com"],
                            "duration_minutes": 15,
                            "time_slot": "today_afternoon"
                        },
                        risk_level="HIGH_RISK_WRITE",
                        requires_approval=True,
                        rationale="Schedule technical alignment to confirm fix rollout.",
                        expected_effect={"summary": "Acme Corp / RECLAIM Incident Post-Mortem & Fix Review", "booked": True},
                        verification_method={"type": "query_events", "summary": "Acme Corp / RECLAIM Incident Post-Mortem & Fix Review"}
                    )
                ],
                remediation_strategy="P0 Bug Escalation + Internal War Room + Direct Executive Engagement with Root Cause Transparency",
                estimated_risk="Low once hotfix is deployed; customer has clear visibility."
            )
            return LLMResponse(parsed=decision, raw_text=decision.model_dump_json(), usage=usage, provider="mock", model=self.model_name)

        # Fallback generic dummy instance
        raw_json = '{"status": "ok"}'
        obj = schema.model_validate_json(raw_json)
        return LLMResponse(parsed=obj, raw_text=raw_json, usage=usage, provider="mock", model=self.model_name)
