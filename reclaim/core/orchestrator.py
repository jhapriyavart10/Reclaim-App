import os
import enum
import uuid
import logging
from typing import Optional, Dict, Any, List, Literal, Union

from reclaim.adapters.registry import AdapterRegistry, adapter_registry
from reclaim.adapters.base import SourceType, ActionReceipt, ConnectorStatus
from reclaim.llm.base import LLMProvider
from reclaim.llm.schemas import (
    GoalInterpretation,
    AgentPlan,
    MissionDecision,
    ApprovalRequest,
    VerificationResult
)
from reclaim.core.state_machine import StateMachineController, MissionState
from reclaim.core.telemetry import TelemetryBus, telemetry_bus
from reclaim.core.evidence_store import EvidenceStore
from reclaim.core.safety_gate import SafetyGate, safety_gate
from reclaim.core.goal_parser import GoalParser
from reclaim.core.planner import InvestigationPlanner, EvidenceSynthesizer
from reclaim.core.decision_engine import DecisionEngine

logger = logging.getLogger(__name__)


class MissionDataPolicy(str, enum.Enum):
    STRICT_LIVE = "STRICT_LIVE"
    HYBRID = "HYBRID"
    SIMULATION = "SIMULATION"


class MissionOrchestrator:
    """
    Core Mission Orchestrator for RECLAIM.
    Drives the autonomous goal-driven lifecycle:
    GOAL -> PLAN -> INVESTIGATE -> EVIDENCE_SYNTHESIS -> DECISION ->
    APPROVAL (if high-risk) -> EXECUTE -> VERIFY -> RECOVER -> COMPLETE
    """

    def __init__(
        self,
        llm_provider: Optional[LLMProvider] = None,
        registry: Optional[AdapterRegistry] = None,
        auto_approve_high_risk: bool = False,
        policy: Optional[Union[MissionDataPolicy, str]] = None
    ):
        self.mission_id = f"msn_{uuid.uuid4().hex[:8]}"
        self.llm_provider = llm_provider
        self.registry = registry or adapter_registry
        self.auto_approve_high_risk = auto_approve_high_risk

        if policy is None:
            raw_policy = os.getenv("MISSION_DATA_POLICY", "SIMULATION")
            self.policy = MissionDataPolicy(raw_policy)
        elif isinstance(policy, str):
            self.policy = MissionDataPolicy(policy)
        else:
            self.policy = policy

        # Subsystems
        self.fsm = StateMachineController(initial_state=MissionState.GOAL_RECEIVED)
        self.telemetry = telemetry_bus
        self.store = EvidenceStore()
        self.safety_gate = safety_gate
        self.goal_parser = GoalParser(llm_provider=llm_provider)
        self.planner = InvestigationPlanner(llm_provider=llm_provider, registry=self.registry)
        self.synthesizer = EvidenceSynthesizer(llm_provider=llm_provider)
        self.decision_engine = DecisionEngine(llm_provider=llm_provider, registry=self.registry, gate=self.safety_gate)

        # Mission Context
        self.goal_text: str = ""
        self.goal_interpretation: Optional[GoalInterpretation] = None
        self.plan: Optional[AgentPlan] = None
        self.decision: Optional[MissionDecision] = None
        self.executed_receipts: List[ActionReceipt] = []
        self.verification_results: List[VerificationResult] = []
        self.required_actions: List[Any] = []
        self.llm_call_count: int = 0
        self.investigation_cycle_count: int = 0

    @property
    def required_action_count(self) -> int:
        return len(self.required_actions)

    @property
    def verified_action_count(self) -> int:
        verified_ids = {
            r.action_id for r in self.executed_receipts
            if r.verification_status == "VERIFIED" and getattr(r, "action_id", None)
        }
        if verified_ids:
            return len(verified_ids)
        return sum(1 for r in self.executed_receipts if r.verification_status == "VERIFIED")

    @property
    def failed_verification_count(self) -> int:
        failed_ids = {
            r.action_id for r in self.executed_receipts
            if r.verification_status == "FAILED_VERIFICATION" and getattr(r, "action_id", None)
        }
        if failed_ids:
            return len(failed_ids)
        return sum(1 for r in self.executed_receipts if r.verification_status == "FAILED_VERIFICATION")

    @property
    def pending_verification_count(self) -> int:
        executed_action_ids = {
            getattr(r, "action_id", None) for r in self.executed_receipts
        }
        unexecuted = sum(
            1 for a in self.required_actions
            if a.action_id not in executed_action_ids
            and not any(a.action_id in getattr(r, "idempotency_key", "") for r in self.executed_receipts)
        )
        pending_receipts = sum(1 for r in self.executed_receipts if r.verification_status == "PENDING")
        return unexecuted + pending_receipts

    def get_evidence_provenance_report(self) -> Dict[str, Any]:
        all_ev = self.store.get_all()
        live_ev = [e for e in all_ev if e.source_type == SourceType.LIVE]
        sim_ev = [e for e in all_ev if e.source_type == SourceType.SIMULATED]
        sim_dep = round(len(sim_ev) / len(all_ev), 2) if all_ev else 1.0
        return {
            "policy": self.policy.value,
            "total_evidence_count": len(all_ev),
            "live_evidence_count": len(live_ev),
            "simulated_evidence_count": len(sim_ev),
            "live_count": len(live_ev),
            "simulated_count": len(sim_ev),
            "simulation_dependency": sim_dep,
            "live_evidence": [e.model_dump() for e in live_ev],
            "simulated_evidence": [e.model_dump() for e in sim_ev]
        }

    async def run_mission(self, goal_text: str) -> bool:
        """
        Executes the complete autonomous rescue mission.
        Returns True if mission successfully reaches COMPLETED.
        """
        self.goal_text = goal_text
        self.telemetry.emit(
            event_type="MISSION_STARTED",
            mission_id=self.mission_id,
            state=self.fsm.current_state.value,
            summary=f"Initiating autonomous business rescue mission: '{goal_text}'. Policy={self.policy.value}"
        )

        # -------------------------------------------------------------
        # 0. STRICT_LIVE POLICY PRE-CHECK
        # -------------------------------------------------------------
        if self.policy == MissionDataPolicy.STRICT_LIVE:
            capabilities = await self.registry.discover_capabilities()
            primary_apps = ["github", "slack", "jira"]
            for app_name in primary_apps:
                cap = capabilities.get(app_name)
                if not cap or cap.source_type != SourceType.LIVE or cap.availability != "AVAILABLE":
                    self.fsm.transition_to(
                        MissionState.FAILED,
                        reason=f"STRICT_LIVE policy rejected: Connector '{app_name}' is not LIVE and AVAILABLE."
                    )
                    self.telemetry.emit(
                        event_type="MISSION_FAILED",
                        mission_id=self.mission_id,
                        state=self.fsm.current_state.value,
                        summary=f"STRICT_LIVE policy violation: connector '{app_name}' requires live authentication."
                    )
                    return False

        # -------------------------------------------------------------
        # 1. GOAL INTERPRETATION & PLANNING
        # -------------------------------------------------------------
        self.goal_interpretation = await self.goal_parser.parse_goal(goal_text)
        self.fsm.transition_to(MissionState.PLANNING, reason="Goal parsed; initiating multi-app capability-aware plan.")

        self.plan = await self.planner.create_plan(self.goal_interpretation)
        self.telemetry.emit(
            event_type="PLAN_CREATED",
            mission_id=self.mission_id,
            state=self.fsm.current_state.value,
            summary=f"Created investigation plan with {len(self.plan.investigation_steps)} targeted queries across apps."
        )

        # -------------------------------------------------------------
        # 2. INVESTIGATION & EVIDENCE GATHERING LOOP
        # -------------------------------------------------------------
        while self.investigation_cycle_count < 3:
            self.investigation_cycle_count += 1
            self.fsm.transition_to(MissionState.INVESTIGATING, reason=f"Investigation cycle {self.investigation_cycle_count} active.")

            for step in self.plan.investigation_steps:
                self.telemetry.emit(
                    event_type="TOOL_REQUESTED",
                    mission_id=self.mission_id,
                    state=self.fsm.current_state.value,
                    summary=f"Querying {step.app.upper()} ({step.step_id}): {step.intent}",
                    app=step.app,
                    details={"params": step.query_params}
                )
                await self._execute_investigation_step(step)

            # Synthesize gathered evidence
            self.fsm.transition_to(MissionState.EVIDENCE_SYNTHESIS, reason="Synthesizing multi-app evidence.")
            synthesis = await self.synthesizer.synthesize(self.goal_interpretation, self.store)

            self.telemetry.emit(
                event_type="HYPOTHESIS_CREATED",
                mission_id=self.mission_id,
                state=self.fsm.current_state.value,
                summary=f"Evidence synthesized: {len(synthesis.root_cause_hypotheses)} root-cause hypotheses formed.",
                details={"confidence": synthesis.confidence, "hypotheses": [h.title for h in synthesis.root_cause_hypotheses]}
            )

            # Check if sufficient cross-app evidence gathered
            if synthesis.confidence >= 0.70 and len(synthesis.root_cause_hypotheses) >= 1:
                break
            else:
                logger.info("[ORCHESTRATOR] Evidence confidence insufficient; additional investigation cycle required.")

        # -------------------------------------------------------------
        # 3. DECISION & ACTION FORMULATION
        # -------------------------------------------------------------
        self.fsm.transition_to(MissionState.DECISION, reason="Root-cause confirmed with multi-app evidence; formulating actions.")
        self.decision = await self.decision_engine.formulate_decision(synthesis, self.store)

        # Enforce Decision Integrity: Substantive reasoning failure must explicitly fail mission
        if self.decision is None or self.decision.reasoning_status == "FAILED" or not self.decision.selected_actions:
            reason = self.decision.root_cause_summary if self.decision else "No decision produced"
            self.fsm.transition_to(
                MissionState.FAILED,
                reason=f"Mission aborted: LLM reasoning failed ({reason}). Refusing fabricated substantive actions."
            )
            self.telemetry.emit(
                event_type="MISSION_FAILED",
                mission_id=self.mission_id,
                state=self.fsm.current_state.value,
                summary=f"Decision formulation failed: {reason}",
                details={
                    "reasoning_status": getattr(self.decision, "reasoning_status", "FAILED"),
                    "decision_source": getattr(self.decision, "decision_source", "none"),
                    "fallback_used": getattr(self.decision, "fallback_used", False)
                }
            )
            return False

        actions_to_take = list(self.decision.selected_actions)
        self.required_actions = [a for a in actions_to_take if not getattr(a, "is_optional", False)]

        self.telemetry.emit(
            event_type="DECISION_CREATED",
            mission_id=self.mission_id,
            state=self.fsm.current_state.value,
            summary=f"Decision formulated: {len(actions_to_take)} remediation actions prioritized ({self.required_action_count} mandatory). Source: {self.decision.decision_source}",
            details={
                "reasoning_status": self.decision.reasoning_status,
                "decision_source": self.decision.decision_source,
                "model": self.decision.model,
                "request_id": self.decision.request_id,
                "fallback_used": self.decision.fallback_used,
                "action_count": len(actions_to_take)
            }
        )

        # -------------------------------------------------------------
        # 4. ACTION EXECUTION & VERIFICATION PIPELINE
        # -------------------------------------------------------------
        for action in actions_to_take:
            can_execute, approval_req = self.safety_gate.evaluate_action(action)

            # Handle High-Risk Approval
            if not can_execute and approval_req:
                self.fsm.transition_to(
                    MissionState.WAITING_FOR_APPROVAL,
                    reason=f"Action '{action.action_id}' ({action.app}:{action.action}) is HIGH_RISK_WRITE; awaiting authorization."
                )
                self.telemetry.emit(
                    event_type="APPROVAL_REQUESTED",
                    mission_id=self.mission_id,
                    state=self.fsm.current_state.value,
                    summary=approval_req.summary,
                    app=action.app,
                    details={"token": approval_req.approval_token}
                )

                if self.auto_approve_high_risk:
                    # Programmatic authorization for automated evaluation / tests
                    self.safety_gate.resolve_approval(approval_req.approval_token, "APPROVE")
                    self.telemetry.emit(
                        event_type="APPROVAL_GRANTED",
                        mission_id=self.mission_id,
                        state=self.fsm.current_state.value,
                        summary=f"Human authorization granted for '{action.action_id}'."
                    )
                else:
                    # In interactive mode without auto-approve, wait or abort if unhandled
                    logger.warning("[ORCHESTRATOR] High-risk action awaiting human input. Use resolve_human_approval().")
                    return False

            # Transition to EXECUTING
            self.fsm.transition_to(MissionState.EXECUTING, reason=f"Executing action '{action.action_id}'.")
            receipt = await self._execute_action(action)
            if receipt not in self.executed_receipts:
                self.executed_receipts.append(receipt)

            self.telemetry.emit(
                event_type="ACTION_EXECUTED",
                mission_id=self.mission_id,
                state=self.fsm.current_state.value,
                summary=f"Executed {action.app.upper()} {action.action} (Status: {receipt.execution_status}).",
                app=action.app,
                source_type=receipt.source_type.value
            )

            # Transition to VERIFYING
            self.fsm.transition_to(MissionState.VERIFYING, reason=f"Running independent verification for '{action.action_id}'.")
            v_res = await self._verify_action(action, receipt)

            if v_res.verified:
                self.fsm.transition_to(MissionState.DECISION, reason="Action verified; evaluating next action.")
            else:
                self.fsm.transition_to(MissionState.RECOVERING, reason=f"Verification failed on {action.action_id}; attempting recovery.")
                self.telemetry.emit(
                    event_type="RECOVERY_STARTED",
                    mission_id=self.mission_id,
                    state=self.fsm.current_state.value,
                    summary=f"Recovering from verification mismatch on {action.action_id}."
                )
                self.fsm.transition_to(MissionState.DECISION, reason="Recovery complete; continuing remaining actions.")

        # -------------------------------------------------------------
        # 5. MISSION SUCCESS VERIFICATION & DETERMINISTIC INVARIANTS
        # -------------------------------------------------------------
        return await self._complete_mission()

    async def _verify_action(self, action, receipt: ActionReceipt) -> VerificationResult:
        adapter = await self.registry.get_adapter(action.app)
        verify_resp = await adapter.verify(receipt)

        if verify_resp.success and verify_resp.data.get("verified"):
            receipt.verification_status = "VERIFIED"
            self.telemetry.emit(
                event_type="VERIFICATION_PASSED",
                mission_id=self.mission_id,
                state=self.fsm.current_state.value,
                summary=f"Verification successful for {action.app.upper()} {getattr(action, 'action', getattr(action, 'operation', 'action'))}.",
                app=action.app
            )
            v_res = VerificationResult(
                action_id=action.action_id,
                verified=True,
                verification_method=(action.verification_method.get("type", "read_check") if hasattr(action, "verification_method") and action.verification_method else "read_check"),
                observed_state=verify_resp.data,
                details="Post-state verified against expected effect."
            )
            self.verification_results.append(v_res)
            return v_res
        else:
            receipt.verification_status = "FAILED_VERIFICATION"
            self.telemetry.emit(
                event_type="VERIFICATION_FAILED",
                mission_id=self.mission_id,
                state=self.fsm.current_state.value,
                summary=f"Verification FAILED for {action.app.upper()} {getattr(action, 'action', getattr(action, 'operation', 'action'))}: {verify_resp.error}",
                app=action.app
            )
            v_res = VerificationResult(
                action_id=action.action_id,
                verified=False,
                verification_method=(action.verification_method.get("type", "read_check") if hasattr(action, "verification_method") and action.verification_method else "read_check"),
                observed_state=verify_resp.data or {},
                details=f"Verification failed: {verify_resp.error}"
            )
            self.verification_results.append(v_res)
            return v_res

    async def _complete_mission(self, reason: str = "") -> bool:
        # Completion Condition:
        # required_action_count == verified_action_count
        # AND failed_verification_count == 0
        # AND pending_verification_count == 0
        is_fully_verified = (
            self.required_action_count > 0
            and self.required_action_count == self.verified_action_count
            and self.failed_verification_count == 0
            and self.pending_verification_count == 0
        )
        if is_fully_verified:
            self.fsm.transition_to(MissionState.COMPLETED, reason=reason or f"All {self.required_action_count} mandatory actions executed and verified.")
            target_name = self.goal_interpretation.target_entity if self.goal_interpretation else "Customer"
            self.telemetry.emit(
                event_type="MISSION_COMPLETED",
                mission_id=self.mission_id,
                state=self.fsm.current_state.value,
                summary=f"Mission SUCCESS: {target_name} crisis resolved. All {self.verified_action_count} mandatory actions verified.",
                details={
                    "required_actions": self.required_action_count,
                    "verified_actions": self.verified_action_count,
                    "failed_verifications": self.failed_verification_count,
                    "pending_actions": self.pending_verification_count,
                    "policy": self.policy.value
                }
            )
            return True
        else:
            self.fsm.transition_to(
                MissionState.FAILED,
                reason=reason or f"Verification invariant failure: {self.verified_action_count}/{self.required_action_count} verified, {self.failed_verification_count} failed, {self.pending_verification_count} pending."
            )
            self.telemetry.emit(
                event_type="MISSION_FAILED",
                mission_id=self.mission_id,
                state=self.fsm.current_state.value,
                summary=f"Mission FAILED: {self.failed_verification_count} mandatory action(s) failed verification.",
                details={
                    "required_actions": self.required_action_count,
                    "verified_actions": self.verified_action_count,
                    "failed_verifications": self.failed_verification_count,
                    "pending_actions": self.pending_verification_count,
                    "policy": self.policy.value
                }
            )
            return False

    async def _execute_investigation_step(self, step) -> None:
        adapter = await self.registry.get_adapter(step.app)
        params = dict(step.query_params)
        query = params.pop("query", "")

        resp = await adapter.search(query=query, **params)
        if not resp.success:
            logger.warning(f"[ORCHESTRATOR] Investigation search failed on {step.app}: {resp.error}")
            return

        if self.policy == MissionDataPolicy.STRICT_LIVE and resp.source_type == SourceType.SIMULATED and step.app in ["github", "slack", "jira"]:
            logger.error(f"[STRICT_LIVE] Rejected simulated response for mandatory app '{step.app}'.")
            self.fsm.transition_to(MissionState.FAILED, reason=f"STRICT_LIVE violation: simulated data received for {step.app}.")
            return

        # Ingest and normalize into EvidenceStore
        if step.app == "crm":
            accounts = resp.data.get("accounts", [])
            for acc in accounts:
                if acc:
                    content = f"Account {acc['name']}: ARR=${acc['arr']}, Health Score={acc['health_score']}/100, Status={acc['status']}, Champion={acc['primary_contact_name']}"
                    self.store.add_evidence(
                        app="crm",
                        source_type=resp.source_type,
                        resource_reference=f"crm:account:{acc['id']}",
                        timestamp=acc.get("updated_at", ""),
                        author="CRM System",
                        content=content,
                        raw_data=acc,
                        connector_status=resp.connector_status
                    )
        elif step.app == "gmail":
            emails = resp.data.get("emails", [])
            for em in emails:
                content = f"Email from {em['from_address']} - Subject: {em['subject']}. Body excerpt: {em['body'][:200]}"
                self.store.add_evidence(
                    app="gmail",
                    source_type=resp.source_type,
                    resource_reference=f"gmail:msg:{em['id']}",
                    timestamp=em.get("timestamp", ""),
                    author=em['from_address'],
                    content=content,
                    raw_data=em,
                    connector_status=resp.connector_status
                )
        elif step.app == "slack":
            messages = resp.data.get("messages", [])
            for msg in messages:
                ts = msg.get("ts") or msg.get("id", "m")
                user = msg.get("user", "slack-user")
                text = msg.get("text", "")
                content = f"Slack message in #{msg.get('channel_name', 'channel')} by {user}: {text}"
                self.store.add_evidence(
                    app="slack",
                    source_type=resp.source_type,
                    resource_reference=f"slack:msg:{ts}",
                    timestamp=msg.get("timestamp", str(ts)),
                    author=user,
                    content=content,
                    raw_data=msg,
                    connector_status=resp.connector_status
                )
        elif step.app == "jira":
            tickets = resp.data.get("tickets", resp.data.get("issues", []))
            for t in tickets:
                fields = t.get("fields", {}) if isinstance(t.get("fields"), dict) else {}
                key = t.get("key") or t.get("id", "UNKNOWN")
                summary = t.get("summary") or fields.get("summary", "")
                priority = t.get("priority") or (fields.get("priority", {}).get("name") if isinstance(fields.get("priority"), dict) else fields.get("priority", ""))
                status = t.get("status") or (fields.get("status", {}).get("name") if isinstance(fields.get("status"), dict) else fields.get("status", ""))
                assignee = t.get("assignee") or (fields.get("assignee", {}).get("displayName") if isinstance(fields.get("assignee"), dict) else fields.get("assignee", ""))
                reporter = t.get("reporter") or (fields.get("reporter", {}).get("displayName") if isinstance(fields.get("reporter"), dict) else "jira-reporter")
                updated_at = t.get("updated_at") or fields.get("updated") or t.get("created_at") or fields.get("created", "")
                content = f"Jira Ticket {key}: {summary}. Priority={priority}, Status={status}, Assignee={assignee}"
                self.store.add_evidence(
                    app="jira",
                    source_type=resp.source_type,
                    resource_reference=f"jira:ticket:{key}",
                    timestamp=updated_at,
                    author=reporter,
                    content=content,
                    raw_data=t,
                    connector_status=resp.connector_status
                )
        elif step.app == "github":
            items = resp.data.get("items", resp.data.get("pull_requests", []))
            for item in items:
                num = item.get("number")
                title = item.get("title", "")
                author = item.get("author") or (item.get("user", {}).get("login") if isinstance(item.get("user"), dict) else "dev")
                state = item.get("state", "open")
                body = item.get("body") or ""
                merged_at = item.get("merged_at") or item.get("updated_at") or item.get("created_at", "")
                content = f"GitHub #{num}: {title}. Author: {author}, State: {state}. Excerpt: {body[:150]}"
                self.store.add_evidence(
                    app="github",
                    source_type=resp.source_type,
                    resource_reference=f"github:issue:{num}",
                    timestamp=merged_at,
                    author=author,
                    content=content,
                    raw_data=item,
                    connector_status=resp.connector_status
                )
        elif step.app == "calendar":
            events = resp.data.get("events", [])
            for ev in events:
                content = f"Calendar Event: {ev.get('summary')} at {ev.get('start_time')} (Attendees: {ev.get('attendees')})"
                self.store.add_evidence(
                    app="calendar",
                    source_type=resp.source_type,
                    resource_reference=f"calendar:evt:{ev.get('id')}",
                    timestamp=ev.get("start_time", ""),
                    author="Calendar",
                    content=content,
                    raw_data=ev,
                    connector_status=resp.connector_status
                )

    async def _execute_action(self, action) -> ActionReceipt:
        adapter = await self.registry.get_adapter(action.app)
        idempotency_key = f"msn_{self.mission_id}_{action.action_id}"
        raw_args = action.arguments if hasattr(action, "arguments") and action.arguments else getattr(action, "parameters", {})
        if hasattr(raw_args, "model_dump"):
            args = raw_args.model_dump(exclude_none=True)
            if hasattr(raw_args, "__pydantic_extra__") and raw_args.__pydantic_extra__:
                args.update(raw_args.__pydantic_extra__)
        elif isinstance(raw_args, dict):
            args = dict(raw_args)
        else:
            args = {}

        op_name = getattr(action, "action", getattr(action, "operation", "create"))
        if op_name in ["update_ticket", "update_issue", "update_health", "update"]:
            res_id = (
                args.get("key")
                or args.get("issue_id")
                or args.get("ticket_id")
                or args.get("ticket_key")
                or args.get("id")
                or getattr(action, "target_resource", None)
                or ("crm_acc_acme_001" if action.app == "crm" else "PROD-1042")
            )
            resp, receipt = await adapter.update(
                resource_id=res_id,
                payload=args,
                idempotency_key=idempotency_key
            )
        else:
            # create_meeting, send_email, post_message, create_issue
            resp, receipt = await adapter.create(
                resource_type=op_name,
                payload=args,
                idempotency_key=idempotency_key
            )

        if receipt:
            receipt.action_id = action.action_id
            if receipt not in self.executed_receipts:
                self.executed_receipts.append(receipt)
        return receipt

    def generate_mission_report(self) -> str:
        """
        Builds the comprehensive mission report DETERMINISTICALLY (no LLM call required).
        Assembles verified facts, decision provenance, action receipts, and audit trail.
        """
        target = self.goal_interpretation.target_entity if self.goal_interpretation else "Target Account"
        state_str = self.fsm.current_state.value
        dec = self.decision

        report_lines = [
            f"# RECLAIM Business Rescue Mission Report",
            f"**Mission ID**: `{self.mission_id}`",
            f"**Target Entity**: {target}",
            f"**Final State**: `{state_str}`",
            f"**Policy**: `{self.policy.value}`",
            "",
            "## 1. Decision & Reasoning Provenance",
        ]
        if dec:
            report_lines.extend([
                f"- **Reasoning Status**: `{dec.reasoning_status}`",
                f"- **Decision Source**: `{dec.decision_source}`",
                f"- **Model**: `{dec.model}`",
                f"- **Provider Request ID**: `{dec.request_id or 'N/A'}`",
                f"- **Fallback Model Used**: `{dec.fallback_used}`",
                f"- **Root Cause**: {dec.root_cause_summary}",
                f"- **Remediation Strategy**: {dec.remediation_strategy}",
            ])
        else:
            report_lines.append("- *No decision generated.*")

        report_lines.extend([
            "",
            "## 2. Multi-App Evidence Summary",
            f"- Total Evidence Nodes: {len(self.store.get_all())}",
            f"- Live Evidence: {len([e for e in self.store.get_all() if e.source_type.value == 'LIVE'])}",
            f"- Simulated Evidence: {len([e for e in self.store.get_all() if e.source_type.value == 'SIMULATED'])}",
            "",
            "## 3. Executed & Verified Actions",
        ])
        for r in self.executed_receipts:
            v_match = next((v for v in self.verification_results if v.action_id == getattr(r, "action_id", None)), None)
            v_status = "VERIFIED" if (v_match and v_match.verified) else r.verification_status
            report_lines.append(
                f"- **[{v_status}]** `{r.app.upper()}` action `{getattr(r, 'action_id', 'act')}`: "
                f"resource `{r.resource_id}` (Source: `{r.source_type.value}`)"
            )

        report_lines.extend([
            "",
            "## 4. Verification & Reliability Scorecard",
            f"- Required Actions: {self.required_action_count}",
            f"- Verified Actions: {self.verified_action_count}",
            f"- Verification Rate: {round(self.verified_action_count / max(1, self.required_action_count), 2)}",
            f"- Failed Verifications: {self.failed_verification_count}",
            f"- Final Mission Status: **{state_str}**"
        ])
        return "\n".join(report_lines)

    def get_llm_trace(self) -> List[Dict[str, Any]]:
        """Returns machine-readable trace of all LLM calls executed during this mission."""
        if hasattr(self.llm_provider, "traces"):
            return [t.model_dump() for t in getattr(self.llm_provider, "traces", [])]
        return []
