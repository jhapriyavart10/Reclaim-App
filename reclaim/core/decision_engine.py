import json
import logging
import uuid
from typing import List, Optional
from reclaim.llm.base import LLMProvider
from reclaim.llm.schemas import (
    MissionDecision,
    ActionProposal,
    EvidenceSynthesisResult
)
from reclaim.core.evidence_store import EvidenceStore
from reclaim.core.safety_gate import SafetyGate, safety_gate
from reclaim.adapters.registry import AdapterRegistry, adapter_registry

logger = logging.getLogger(__name__)


class DecisionEngine:
    """
    Deterministic Decision Engine evaluating hypotheses and producing validated ActionProposals.
    Ensures that every proposed remediation action is backed by real evidence and registered capabilities.
    """

    def __init__(
        self,
        llm_provider: Optional[LLMProvider] = None,
        registry: Optional[AdapterRegistry] = None,
        gate: Optional[SafetyGate] = None
    ):
        self.llm_provider = llm_provider
        self.registry = registry or adapter_registry
        self.safety_gate = gate or safety_gate

    async def formulate_decision(
        self,
        synthesis: EvidenceSynthesisResult,
        store: EvidenceStore
    ) -> MissionDecision:
        capabilities = await self.registry.get_all_capabilities()

        if self.llm_provider and synthesis.root_cause_hypotheses:
            prompt = (
                f"ROOT CAUSE SYNTHESIS:\n{json.dumps(synthesis.model_dump(), indent=2)}\n\n"
                "Formulate prioritized remediation actions to resolve the incident and de-escalate customer churn.\n"
                "CRITICAL INVARIANTS:\n"
                "- Internal actions (escalate Jira ticket, internal Slack alert) have risk_level='LOW_RISK_WRITE'.\n"
                "- Customer-facing actions (direct email to customer CTO, booking customer calendar) have risk_level='HIGH_RISK_WRITE'.\n"
                "- Every action must have expected_effect and verification_method."
            )
            try:
                resp = await self.llm_provider.generate_structured(schema=MissionDecision, prompt=prompt)
                validated_decision = self._validate_and_sanitize_decision(resp.parsed, capabilities)
                return validated_decision
            except Exception as e:
                logger.error(f"[DECISION INTEGRITY] LLM decision formulation failed ({e}). Refusing substantive deterministic fallback.")
                active_model = getattr(self.llm_provider, "last_model_used", getattr(self.llm_provider, "model_name", "unknown"))
                fallback_used = getattr(self.llm_provider, "last_fallback_used", False)
                return MissionDecision(
                    decision_id=f"dec_failed_{uuid.uuid4().hex[:8]}",
                    reasoning_status="FAILED",
                    decision_source="none",
                    model=active_model,
                    request_id=getattr(self.llm_provider, "last_request_id", ""),
                    fallback_used=fallback_used,
                    root_cause_summary="LLM reasoning failed: substantive root cause could not be established by LLM.",
                    hypotheses=[],
                    selected_actions=[],
                    remediation_strategy="None (Substantive reasoning failed)",
                    estimated_risk="HIGH"
                )

        # Deterministic control mode (only when NO LLM provider is configured)
        return self._deterministic_decision(synthesis, store, capabilities)

    def _validate_and_sanitize_decision(self, decision: MissionDecision, capabilities: dict) -> MissionDecision:
        return self._validate_decision_actions(decision, capabilities)

    def _validate_decision_actions(
        self,
        decision: MissionDecision,
        capabilities: dict
    ) -> MissionDecision:
        valid_actions: List[ActionProposal] = []
        for action in decision.selected_actions:
            app = action.app.lower()
            if app not in capabilities:
                logger.warning(f"[DECISION ENGINE] Dropping action {action.action_id}: unregistered app '{app}'")
                continue

            # Deterministic safety gate classification
            enforced_risk = self.safety_gate.classify_risk(app, action.action)
            action.risk_level = enforced_risk.value  # type: ignore
            action.requires_approval = (enforced_risk.value == "HIGH_RISK_WRITE")

            valid_actions.append(action)

        decision.selected_actions = valid_actions
        return decision

    def _deterministic_decision(self, synthesis: EvidenceSynthesisResult, store: EvidenceStore, capabilities: Optional[dict] = None) -> MissionDecision:
        import re
        import os
        from pathlib import Path
        hyp = synthesis.root_cause_hypotheses[0] if synthesis.root_cause_hypotheses else None
        hyp_title = hyp.title if hyp else "Production Incident"
        hyp_desc = hyp.description if hyp else "Operational disruption"

        caps = capabilities or {}

        # Inspect evidence items in store to identify specific tickets, channels, and emails
        all_ev = store.get_all()
        discovered_tickets: List[str] = []
        discovered_channels: List[str] = []
        discovered_emails: List[str] = []
        ticket_pattern = re.compile(r'\b[A-Z]{2,10}-\d+\b')
        email_pattern = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b')

        # Check if hypothesis explicitly identified a tracking ticket
        hyp_tickets = ticket_pattern.findall(hyp_desc + " " + hyp_title)
        for ht in hyp_tickets:
            if ht not in discovered_tickets:
                discovered_tickets.append(ht)

        for e in all_ev:
            content_lower = e.content.lower()
            is_closed = "status=closed" in content_lower or "status=resolved" in content_lower
            for t in ticket_pattern.findall(e.content):
                if not is_closed and t not in discovered_tickets:
                    discovered_tickets.append(t)
            for em in email_pattern.findall(e.content):
                if em not in discovered_emails and not em.endswith(".internal"):
                    discovered_emails.append(em)

            # Check Slack channels
            if e.app == "slack":
                channel = e.raw_data.get("channel_name") or e.raw_data.get("channel")
                if channel and channel not in discovered_channels:
                    discovered_channels.append(channel)

        # Check live demo manifest only in explicit live policy mode
        if not discovered_tickets and (os.getenv("MISSION_DATA_POLICY") == "STRICT_LIVE" or os.getenv("RECLAIM_LIVE_DEMO_SEED") == "true"):
            manifest_p = Path("artifacts/live_demo_seed.json")
            if manifest_p.exists():
                try:
                    with open(manifest_p, "r", encoding="utf-8") as f:
                        m_data = json.load(f)
                    m_key = m_data.get("resources", {}).get("jira", {}).get("issue_key")
                    if m_key and not m_data.get("cleanup_completed", False):
                        discovered_tickets.append(m_key)
                except Exception:
                    pass

        actions: List[ActionProposal] = []
        test_project = os.getenv("JIRA_TEST_PROJECT") or "PROD"

        # 1. Jira Action: Update existing ticket if found, or create new ticket
        if discovered_tickets:
            primary_ticket = discovered_tickets[0]
            actions.append(ActionProposal(
                action_id=f"act_jira_{primary_ticket.lower().replace('-', '_')}_escalation",
                app="jira",
                action="update_ticket",
                arguments={
                    "key": primary_ticket,
                    "priority": "High",
                    "assignee": "tech_lead_alex"
                },
                risk_level="LOW_RISK_WRITE",
                requires_approval=False,
                rationale=f"Escalate tracked issue {primary_ticket} to High priority based on verified causal evidence.",
                expected_effect={"priority": "High", "assignee": "tech_lead_alex"},
                verification_method={"type": "query_issue", "key": primary_ticket}
            ))
        else:
            actions.append(ActionProposal(
                action_id="act_jira_create_p0_incident",
                app="jira",
                action="create_ticket",
                arguments={
                    "project": test_project,
                    "summary": f"P0 Incident: {hyp_title}",
                    "priority": "High",
                    "description": hyp_desc
                },
                risk_level="LOW_RISK_WRITE",
                requires_approval=False,
                rationale="Create tracking bug ticket since no prior ticket exists for this incident.",
                expected_effect={"project": test_project, "priority": "High"},
                verification_method={"type": "query_issue", "project": test_project}
            ))

        # 2. Slack Action: Post to test channel or discovered channel
        target_channel = os.getenv("SLACK_TEST_CHANNEL") or (discovered_channels[0] if discovered_channels else "alerts-enterprise")
        actions.append(ActionProposal(
            action_id="act_slack_war_room",
            app="slack",
            action="post_message",
            arguments={
                "channel": target_channel,
                "text": f":rotating_light: P0 Incident Triage: {hyp_title}. Remediation initiated."
            },
            risk_level="LOW_RISK_WRITE",
            requires_approval=False,
            rationale=f"Notify incident response team and stakeholders in #{target_channel}.",
            expected_effect={"channel": target_channel, "posted": True},
            verification_method={"type": "query_channel_history", "channel": target_channel}
        ))

        # 3. High-Risk Customer Email (ONLY if gmail capability is present)
        if "gmail" in caps:
            contact_email = discovered_emails[0] if discovered_emails else None
            if contact_email:
                actions.append(ActionProposal(
                    action_id="act_email_customer_rca",
                    app="gmail",
                    action="send_email",
                    arguments={
                        "to": contact_email,
                        "subject": f"Executive Incident Update & Remediation: {hyp_title}",
                        "body": (
                            f"Dear Customer Partner,\n\n"
                            f"Our engineering and incident response teams have completed root-cause analysis: {hyp_desc}.\n"
                            f"Remediation actions are underway and service stability is being actively restored.\n\n"
                            f"Incident Commander | Enterprise Support"
                        )
                    },
                    risk_level="HIGH_RISK_WRITE",
                    requires_approval=True,
                    rationale="External customer-facing communication to provide technical transparency.",
                    expected_effect={"to": contact_email, "is_sent": True},
                    verification_method={"type": "query_sent_box", "to": contact_email}
                ))

        return MissionDecision(
            decision_id=f"dec_ctrl_{len(actions)}_actions",
            reasoning_status="DETERMINISTIC_CONTROL",
            decision_source="deterministic_control",
            model="none",
            request_id="",
            fallback_used=False,
            root_cause_summary=hyp_desc,
            hypotheses=synthesis.root_cause_hypotheses,
            selected_actions=actions,
            remediation_strategy="P0 Ticket Escalation -> Internal Incident Channel -> Customer Alignment",
            estimated_risk="Low once remediation actions are verified."
        )
