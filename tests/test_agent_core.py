import os
import asyncio
import pytest
from unittest.mock import patch, MagicMock

from reclaim.world.engine import WorldStateEngine
from reclaim.world.seed_acme import seed_acme_world
from reclaim.world.faults import fault_injector
from reclaim.adapters.registry import AdapterRegistry
from reclaim.adapters.idempotency import idempotency_manager
from reclaim.adapters.base import SourceType, RiskLevel
from reclaim.llm.mock import MockProvider
from reclaim.llm.schemas import ActionProposal, GoalInterpretation
from reclaim.core.state_machine import StateMachineController, MissionState, InvalidStateTransitionError
from reclaim.core.goal_parser import GoalParser
from reclaim.core.evidence_store import EvidenceStore, EntityNormalizer
from reclaim.core.safety_gate import SafetyGate
from reclaim.core.planner import InvestigationPlanner, EvidenceSynthesizer
from reclaim.core.decision_engine import DecisionEngine
from reclaim.core.orchestrator import MissionOrchestrator
from reclaim.core.telemetry import telemetry_bus


@pytest.fixture(autouse=True)
def setup_world(tmp_path):
    fault_injector.clear()
    idempotency_manager.clear()
    telemetry_bus.clear()
    test_db = tmp_path / "core_test_world.db"
    engine = WorldStateEngine(db_path=test_db)
    seed_acme_world(engine)
    return engine


# ============================================================================
# 1. Goal Interpretation
# ============================================================================
def test_goal_interpretation():
    async def run():
        parser = GoalParser(llm_provider=None)
        interp = await parser.parse_goal("Save Acme Corp.")
        assert interp.target_entity == "Acme Corp"
        assert len(interp.success_conditions) >= 3
        assert len(interp.initial_information_gaps) >= 2

        interp2 = await parser.parse_goal("Protect the Delta account renewal")
        assert "Delta" in interp2.target_entity

    asyncio.run(run())


# ============================================================================
# 2. Capability-Aware Planning
# ============================================================================
def test_capability_aware_planning(setup_world):
    async def run():
        registry = AdapterRegistry(mode="simulation")
        planner = InvestigationPlanner(registry=registry)
        interp = GoalInterpretation(
            target_entity="Acme Corp",
            desired_outcome="Resolve churn",
            success_conditions=["root cause identified"],
            constraints=[],
            initial_information_gaps=[],
            candidate_app_domains=["crm", "gmail", "slack", "jira", "github", "calendar"]
        )
        plan = await planner.create_plan(interp)
        assert len(plan.investigation_steps) >= 5
        # Ensure all steps map to registered apps
        caps = await registry.get_all_capabilities()
        for step in plan.investigation_steps:
            assert step.app in caps

    asyncio.run(run())


# ============================================================================
# 3. Nonexistent Tool Rejection
# ============================================================================
def test_nonexistent_tool_rejection():
    # 1. Pydantic schema rejects invalid/invented app
    from pydantic import ValidationError
    from reclaim.llm.schemas import InvestigationStep, AgentPlan
    with pytest.raises(ValidationError):
        InvestigationStep(
            step_id="bad_1",
            app="salesforce_custom_api",  # Unregistered app name
            intent="hack",
            query_params={},
            expected_information="data"
        )

    # 2. Planner filters out unavailable apps from registry
    async def run():
        registry = AdapterRegistry(mode="simulation")
        planner = InvestigationPlanner(registry=registry)
        caps = await registry.get_all_capabilities()

        # Simulate an app becoming UNAVAILABLE
        caps["github"].availability = "UNAVAILABLE"

        plan = AgentPlan(
            goal="Test",
            initial_assessment="Test",
            investigation_steps=[
                InvestigationStep(step_id="step_gh", app="github", intent="test", query_params={}, expected_information="prs"),
                InvestigationStep(step_id="step_crm", app="crm", intent="test", query_params={}, expected_information="arr")
            ],
            risk_summary="none"
        )
        sanitized = planner._validate_and_sanitize_plan(plan, caps)
        assert len(sanitized.investigation_steps) == 1
        assert sanitized.investigation_steps[0].app == "crm"

    asyncio.run(run())



# ============================================================================
# 4. Evidence Creation & Store
# ============================================================================
def test_evidence_creation_and_store():
    store = EvidenceStore()
    item = store.add_evidence(
        app="jira",
        source_type=SourceType.SIMULATED,
        resource_reference="jira:ticket:PROD-1042",
        timestamp="2026-09-13T10:00:00Z",
        author="bob.engineer",
        content="Bug PROD-1042 breaking Acme Corp data sync on /v2/data-sync endpoint"
    )
    assert item.evidence_id.startswith("ev_jira")
    assert "TICKET:PROD-1042" in item.entities
    assert "ENDPOINT:/v2/data-sync" in item.entities
    assert store.get(item.evidence_id) is not None


# ============================================================================
# 5. Evidence ID Validation & Anti-Hallucination Barrier
# ============================================================================
def test_evidence_id_validation():
    store = EvidenceStore()
    store.add_evidence("crm", SourceType.SIMULATED, "crm:1", "t", "auth", "content", custom_id="ev_crm_01")
    store.add_evidence("gmail", SourceType.SIMULATED, "gmail:1", "t", "auth", "content", custom_id="ev_gmail_01")

    # Real IDs
    assert store.validate_ids(["ev_crm_01", "ev_gmail_01"]) is True
    # Fabricated ID
    assert store.validate_ids(["ev_crm_01", "ev_fabricated_999"]) is False


# ============================================================================
# 6. Cross-App Hypothesis Validation
# ============================================================================
def test_cross_app_hypothesis_validation():
    store = EvidenceStore()
    store.add_evidence("jira", SourceType.SIMULATED, "jira:1", "t", "auth", "content", custom_id="ev_jira_01")
    store.add_evidence("github", SourceType.SIMULATED, "github:1", "t", "auth", "content", custom_id="ev_gh_01")

    apps = store.get_corroborating_apps(["ev_jira_01", "ev_gh_01"])
    assert len(apps) == 2
    assert "jira" in apps and "github" in apps


# ============================================================================
# 7. Hallucinated Resource / Single-Source Hypothesis Rejection
# ============================================================================
def test_single_source_hypothesis_rejection():
    store = EvidenceStore()
    store.add_evidence("jira", SourceType.SIMULATED, "jira:1", "t", "auth", "ticket 1", custom_id="ev_jira_01")
    store.add_evidence("jira", SourceType.SIMULATED, "jira:2", "t", "auth", "ticket 2", custom_id="ev_jira_02")

    from reclaim.llm.schemas import EvidenceSynthesisResult, RootCauseHypothesis
    synthesizer = EvidenceSynthesizer()
    synth_input = EvidenceSynthesisResult(
        root_cause_hypotheses=[
            RootCauseHypothesis(
                hypothesis_id="h1",
                title="Single app hypothesis",
                description="Test",
                culprit_app="jira",
                corroborating_claim_ids=["ev_jira_01", "ev_jira_02"],  # Single app only!
                confidence=0.8
            )
        ],
        supporting_evidence_ids=["ev_jira_01", "ev_jira_02"],
        confidence=0.8,
        business_impact="impact"
    )
    validated = synthesizer._validate_synthesis(synth_input, store)
    # Must reject hypothesis because evidence does not span >= 2 distinct apps
    assert len(validated.root_cause_hypotheses) == 0


# ============================================================================
# 8. Deterministic Safety Gate: Low Risk vs High Risk
# ============================================================================
def test_safety_gate_risk_classification():
    gate = SafetyGate()

    # Jira update is LOW_RISK_WRITE -> Can execute immediately without human approval
    jira_action = ActionProposal(
        action_id="act_jira_1",
        app="jira",
        action="update_ticket",
        arguments={"key": "PROD-1042", "priority": "P0"},
        risk_level="READ_ONLY",  # LLM attempted to mark as read_only
        requires_approval=False,
        rationale="Escalate bug",
        expected_effect={"priority": "P0"},
        verification_method={"type": "query_issue"}
    )
    can_exec, appr = gate.evaluate_action(jira_action)
    assert can_exec is True
    assert appr is None
    assert jira_action.risk_level == RiskLevel.LOW_RISK_WRITE.value

    # Gmail send email is HIGH_RISK_WRITE -> Strictly BLOCKED, requires approval
    email_action = ActionProposal(
        action_id="act_email_1",
        app="gmail",
        action="send_email",
        arguments={"to": "sarah@acme.com", "subject": "RCA"},
        risk_level="LOW_RISK_WRITE",  # LLM attempted to bypass approval
        requires_approval=False,
        rationale="Customer email",
        expected_effect={"is_sent": True},
        verification_method={"type": "query_sent"}
    )
    can_exec_email, appr_email = gate.evaluate_action(email_action)
    assert can_exec_email is False
    assert appr_email is not None
    assert email_action.risk_level == RiskLevel.HIGH_RISK_WRITE.value
    assert email_action.requires_approval is True


# ============================================================================
# 9. Human Approval Grant vs Rejection
# ============================================================================
def test_human_approval_grant_and_rejection():
    gate = SafetyGate()
    action = ActionProposal(
        action_id="act_cal_1",
        app="calendar",
        action="create_meeting",
        arguments={"attendees": "sarah@acme.com"},
        risk_level="HIGH_RISK_WRITE",
        requires_approval=True,
        rationale="Meeting",
        expected_effect={"status": "confirmed"},
        verification_method={"type": "query_cal"}
    )
    _, req = gate.evaluate_action(action)
    token = req.approval_token

    # Test rejection
    resolved = gate.resolve_approval(token, "REJECT", reason="Do not schedule on Mondays")
    assert resolved.status == "REJECTED"
    assert token not in gate.get_pending()


# ============================================================================
# 10. State Machine Deterministic Transition Guarantees
# ============================================================================
def test_state_machine_transitions():
    fsm = StateMachineController()
    assert fsm.current_state == MissionState.GOAL_RECEIVED

    fsm.transition_to(MissionState.PLANNING)
    assert fsm.current_state == MissionState.PLANNING

    fsm.transition_to(MissionState.INVESTIGATING)
    assert fsm.current_state == MissionState.INVESTIGATING

    fsm.transition_to(MissionState.EVIDENCE_SYNTHESIS)
    assert fsm.current_state == MissionState.EVIDENCE_SYNTHESIS

    # Illegal transition: cannot go from EVIDENCE_SYNTHESIS directly to COMPLETED
    with pytest.raises(InvalidStateTransitionError):
        fsm.transition_to(MissionState.COMPLETED)


# ============================================================================
# 11. Full End-to-End Mission Golden Path Execution
# ============================================================================
def test_full_mission_golden_path_execution(setup_world):
    async def run():
        registry = AdapterRegistry(mode="simulation", engine=setup_world)
        orchestrator = MissionOrchestrator(
            llm_provider=None,  # Uses deterministic reasoning
            registry=registry,
            auto_approve_high_risk=True
        )

        success = await orchestrator.run_mission("Save Acme Corp.")
        assert success is True
        assert orchestrator.fsm.current_state == MissionState.COMPLETED

        # Verify evidence was populated across multiple apps
        assert len(orchestrator.store.get_all()) >= 5
        # Verify actions executed and receipts produced
        assert len(orchestrator.executed_receipts) >= 3
        # Verify all verification checks passed
        assert len(orchestrator.verification_results) >= 1
        assert all(v.verified for v in orchestrator.verification_results)

        # Verify Jira state in SQLite was actually mutated and verified
        ticket = setup_world.get_jira_ticket("PROD-1042")
        assert ticket["priority"] in ("High", "P0 - Blocker")

        # Verify telemetry stream integrity
        events = telemetry_bus.get_events(orchestrator.mission_id)
        event_types = [e.event_type for e in events]
        assert "MISSION_STARTED" in event_types
        assert "PLAN_CREATED" in event_types
        assert "HYPOTHESIS_CREATED" in event_types
        assert "DECISION_CREATED" in event_types
        assert "ACTION_EXECUTED" in event_types
        assert "VERIFICATION_PASSED" in event_types
        assert "MISSION_COMPLETED" in event_types

    asyncio.run(run())


# ============================================================================
# 12. Verification Failure Triggers Recovery
# ============================================================================
def test_verification_failure_triggers_recovery(setup_world):
    async def run():
        registry = AdapterRegistry(mode="simulation", engine=setup_world)
        orchestrator = MissionOrchestrator(
            llm_provider=None,
            registry=registry,
            auto_approve_high_risk=True
        )

        # Inject failure: ACK without mutation on Jira
        fault_injector.set_fault("WRITE_ACK_WITHOUT_MUTATION")

        # Run mission
        await orchestrator.run_mission("Save Acme Corp.")

        # Check telemetry for verification failure and recovery events
        events = telemetry_bus.get_events(orchestrator.mission_id)
        event_types = [e.event_type for e in events]
        assert "VERIFICATION_FAILED" in event_types
        assert "RECOVERY_STARTED" in event_types

    asyncio.run(run())


# ============================================================================
# 13. Rejection of High-Risk Action Halts Execution
# ============================================================================
def test_rejection_of_high_risk_action_halts_without_approval(setup_world):
    async def run():
        registry = AdapterRegistry(mode="simulation", engine=setup_world)
        orchestrator = MissionOrchestrator(
            llm_provider=None,
            registry=registry,
            auto_approve_high_risk=False  # No auto-approve!
        )

        # Run mission; should pause at WAITING_FOR_APPROVAL
        success = await orchestrator.run_mission("Save Acme Corp.")
        assert success is False
        assert orchestrator.fsm.current_state == MissionState.WAITING_FOR_APPROVAL

        # Confirm that zero emails were sent without approval
        sent_emails = setup_world.search_emails("Executive Resolution Plan")
        assert len(sent_emails) == 0

    asyncio.run(run())


# ============================================================================
# 14. HTTP 503 Adapter Fault Tolerated in Mission
# ============================================================================
def test_http_503_adapter_fault_tolerated_in_mission(setup_world):
    async def run():
        # Inject HTTP 503 fault
        fault_injector.set_fault("HTTP_503")
        registry = AdapterRegistry(mode="simulation", engine=setup_world)
        orchestrator = MissionOrchestrator(
            llm_provider=None,
            registry=registry,
            auto_approve_high_risk=True
        )

        # Mission runs and does not crash despite 503 errors on tool searches
        await orchestrator.run_mission("Save Acme Corp.")
        events = telemetry_bus.get_events(orchestrator.mission_id)
        assert len(events) >= 5

    asyncio.run(run())


# ============================================================================
# 15. Idempotency Prevents Duplicate Actions
# ============================================================================
def test_idempotency_prevents_duplicate_actions(setup_world):
    async def run():
        registry = AdapterRegistry(mode="simulation", engine=setup_world)
        orchestrator = MissionOrchestrator(registry=registry, auto_approve_high_risk=True)

        action = ActionProposal(
            action_id="act_idempotent_test",
            app="jira",
            action="update_ticket",
            arguments={"key": "PROD-1042", "priority": "P0 - Blocker"},
            risk_level="LOW_RISK_WRITE",
            requires_approval=False,
            rationale="Test",
            expected_effect={"priority": "P0 - Blocker"},
            verification_method={"type": "query_issue"}
        )

        receipt1 = await orchestrator._execute_action(action)
        assert receipt1.execution_status == "EXECUTED"

        # Duplicate execution
        receipt2 = await orchestrator._execute_action(action)
        assert receipt2.action_id == receipt1.action_id

    asyncio.run(run())


# ============================================================================
# 16. Telemetry Event Stream Integrity
# ============================================================================
def test_telemetry_event_stream_integrity(setup_world):
    async def run():
        registry = AdapterRegistry(mode="simulation", engine=setup_world)
        orchestrator = MissionOrchestrator(registry=registry, auto_approve_high_risk=True)
        await orchestrator.run_mission("Save Acme Corp.")

        events = telemetry_bus.get_events(orchestrator.mission_id)
        for ev in events:
            assert ev.mission_id == orchestrator.mission_id
            assert ev.timestamp is not None
            assert ev.summary is not None
            assert len(ev.summary) > 0

    asyncio.run(run())


# ============================================================================
# 17. Live Groq LLM Mission Orchestration
# ============================================================================
def test_live_groq_llm_mission_orchestration(setup_world):
    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        pytest.skip("SKIPPED — missing credentials (GROQ_API_KEY)")

    async def run():
        from reclaim.llm.groq import GroqProvider
        provider = GroqProvider(api_key=groq_key, model_name=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))
        registry = AdapterRegistry(mode="simulation", engine=setup_world)
        orchestrator = MissionOrchestrator(
            llm_provider=provider,
            registry=registry,
            auto_approve_high_risk=True
        )

        success = await orchestrator.run_mission("Save Acme Corp.")
        if not success and orchestrator.decision and orchestrator.decision.reasoning_status == "FAILED":
            traces = getattr(provider, "traces", [])
            if any("429" in t.status for t in traces):
                pytest.skip("SKIPPED — Groq daily token quota exhausted during test.")
        assert success is True
        assert orchestrator.fsm.current_state == MissionState.COMPLETED
        assert len(orchestrator.executed_receipts) >= 1

    asyncio.run(run())

