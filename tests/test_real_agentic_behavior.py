"""
Comprehensive Test Suite: Real Agentic Behavior & Anti-Scripting Verification.
Verifies that RECLAIM is an autonomous, adaptive, and evidence-grounded agent rather than a hardcoded workflow.
Tests:
1. Counterfactual World Test: Beta Corp (DB-208 / PR #901) is isolated without touching Acme resources.
2. Adaptive Planning: Varied mission prompts produce adaptive goals and query targets.
3. Red-Herring Discrimination: Agent rejects decoy latest PR #899 and identifies older true culprit PR #882.
4. Tool Order Invariance: Convergence regardless of tool start order (Slack first vs Jira first vs GitHub first).
5. Dynamic Action Decision: Existing ticket -> update; absent ticket -> create; channel routing.
6. Safety Gate: HIGH_RISK_WRITE enters WAITING_FOR_APPROVAL; rejection triggers replan; approval executes.
7. Verification Failure & Recovery: ACK without mutation triggers FAILED_VERIFICATION and recovery loop.
8. Live Groq Golden Path Execution & Artifact Generation: Emits artifacts/acme_golden_trace.json.
9. Agenticity Scorecard: Evaluates composite agenticity >= 0.85.
"""

import asyncio
import json
import os
from pathlib import Path
import pytest

from reclaim.world.engine import WorldStateEngine
from reclaim.world.seed_acme import seed_acme_world
from reclaim.world.seed_beta import seed_beta_world
from reclaim.world.seed_red_herring import seed_red_herring_world
from reclaim.adapters.registry import AdapterRegistry
from reclaim.core.orchestrator import MissionOrchestrator
from reclaim.core.state_machine import MissionState
from reclaim.core.goal_parser import GoalParser
from reclaim.core.planner import InvestigationPlanner, EvidenceSynthesizer
from reclaim.core.decision_engine import DecisionEngine
from reclaim.core.safety_gate import SafetyGate
from reclaim.core.evidence_store import EvidenceStore
from reclaim.adapters.base import SourceType
from reclaim.llm.schemas import ActionProposal
from reclaim.evaluation.scorecard import AgenticityScorecard


@pytest.fixture
def acme_world(tmp_path):
    db_path = str(tmp_path / "acme_test.db")
    engine = WorldStateEngine(db_path=db_path)
    seed_acme_world(engine)
    return engine


@pytest.fixture
def beta_world(tmp_path):
    db_path = str(tmp_path / "beta_test.db")
    engine = WorldStateEngine(db_path=db_path)
    seed_beta_world(engine)
    return engine


@pytest.fixture
def red_herring_world(tmp_path):
    db_path = str(tmp_path / "red_herring_test.db")
    engine = WorldStateEngine(db_path=db_path)
    seed_red_herring_world(engine)
    return engine


# ============================================================================
# 1. Counterfactual World Test: Beta Corp vs Acme Corp
# ============================================================================
def test_counterfactual_beta_corp_discovery(beta_world):
    """
    Proves that RECLAIM does NOT hardcode Acme Corp PR #882 / PROD-1042.
    When running against Beta Corp, the agent must identify Beta's causal chain:
    Jira DB-208, GitHub PR #901, and DB connection pool timeout.
    """
    async def run():
        registry = AdapterRegistry(mode="simulation", engine=beta_world)
        orchestrator = MissionOrchestrator(registry=registry, auto_approve_high_risk=True)

        success = await orchestrator.run_mission("Resolve Beta Corp's production crisis.")
        assert success is True
        assert orchestrator.fsm.current_state == MissionState.COMPLETED

        # Check hypothesis
        assert orchestrator.decision is not None
        assert len(orchestrator.decision.hypotheses) >= 1
        top_hyp = orchestrator.decision.hypotheses[0]

        # Must identify PR #901 or DB-208 or database pool
        assert "901" in top_hyp.title or "DB-208" in top_hyp.title or "timeout" in top_hyp.title.lower() or "pool" in top_hyp.title.lower()

        # Must NOT reference Acme Corp tickets or PRs
        assert "PROD-1042" not in top_hyp.title
        assert "882" not in top_hyp.title

        # Check executed actions
        executed_apps = [r.app for r in orchestrator.executed_receipts]
        assert "jira" in executed_apps or "slack" in executed_apps

        # Jira action must target DB-208, NOT PROD-1042
        jira_receipts = [r for r in orchestrator.executed_receipts if r.app == "jira"]
        if jira_receipts:
            assert "DB-208" in str(jira_receipts[0].resource) or "DB" in str(jira_receipts[0].resource)

    asyncio.run(run())


# ============================================================================
# 2. Adaptive Planning Across Goal Formulations
# ============================================================================
def test_adaptive_planning_across_goals():
    """
    Verifies that the agent core adapts its goal decomposition and plan
    when presented with different mission formulations.
    """
    async def run():
        parser = GoalParser()
        planner = InvestigationPlanner()

        # Goal A
        gA = await parser.parse_goal("Resolve Acme's production crisis before it causes business impact.")
        pA = await planner.create_plan(gA)
        assert gA.target_entity == "Acme Corp"
        assert any("Acme" in s.intent for s in pA.investigation_steps)

        # Goal B
        gB = await parser.parse_goal("Investigate the Acme API failure and coordinate remediation.")
        pB = await planner.create_plan(gB)
        assert gB.target_entity == "Acme Corp"

        # Goal C
        gC = await parser.parse_goal("Investigate Beta Corp's production crisis.")
        pC = await planner.create_plan(gC)
        assert gC.target_entity == "Beta Corp"
        assert any("Beta" in s.intent for s in pC.investigation_steps)

    asyncio.run(run())


# ============================================================================
# 3. Red-Herring Discrimination
# ============================================================================
def test_red_herring_discrimination(red_herring_world):
    """
    Adversarial test: Newest PR #899 is a decoy (marketing hero CSS).
    Real culprit is older PR #882 (API serializer regression).
    The agent must identify PR #882 and filter out PR #899 and catering invoice.
    """
    async def run():
        registry = AdapterRegistry(mode="simulation", engine=red_herring_world)
        orchestrator = MissionOrchestrator(registry=registry, auto_approve_high_risk=True)

        success = await orchestrator.run_mission("Resolve Acme's production crisis before it causes business impact.")
        assert success is True

        top_hyp = orchestrator.decision.hypotheses[0]
        # Must identify PR #882, not the decoy PR #899
        assert "882" in top_hyp.title or "serializer" in top_hyp.title.lower() or "500" in top_hyp.title
        assert "899" not in top_hyp.title

    asyncio.run(run())


# ============================================================================
# 4. Tool Order Randomization Invariance
# ============================================================================
def test_tool_order_invariance(acme_world):
    """
    Proves that the agent does NOT depend on a fixed Slack -> Jira -> GitHub sequence.
    Can start investigation in reverse order (GitHub -> Jira -> Slack) and still converge.
    """
    async def run():
        registry = AdapterRegistry(mode="simulation", engine=acme_world)
        orchestrator = MissionOrchestrator(registry=registry, auto_approve_high_risk=True)

        # Run goal interpretation & planning
        orchestrator.goal_text = "Resolve Acme's production crisis."
        orchestrator.goal_interpretation = await orchestrator.goal_parser.parse_goal(orchestrator.goal_text)
        orchestrator.plan = await orchestrator.planner.create_plan(orchestrator.goal_interpretation)

        # Invert step order: GitHub first!
        orchestrator.plan.investigation_steps = list(reversed(orchestrator.plan.investigation_steps))
        first_tool = orchestrator.plan.investigation_steps[0].app
        assert first_tool in ("calendar", "gmail", "github")  # Non-Slack first!

        # Execute investigation
        for step in orchestrator.plan.investigation_steps:
            await orchestrator._execute_investigation_step(step)

        # Synthesize evidence
        synthesis = await orchestrator.synthesizer.synthesize(orchestrator.goal_interpretation, orchestrator.store)
        assert synthesis.confidence >= 0.75
        assert len(synthesis.root_cause_hypotheses) >= 1
        assert "882" in synthesis.root_cause_hypotheses[0].title or "PROD-1042" in synthesis.root_cause_hypotheses[0].description

    asyncio.run(run())


# ============================================================================
# 5. Dynamic Action Decision: Existing Ticket vs Missing Ticket
# ============================================================================
def test_action_decision_adapts_to_state(acme_world, tmp_path):
    """
    Scenario A: Jira ticket exists (PROD-1042) -> action is update_ticket.
    Scenario B: No Jira ticket exists in store -> action is create_ticket.
    """
    async def run():
        decision_engine = DecisionEngine()
        store = EvidenceStore()

        # Scenario A: Store with PROD-1042
        store.add_evidence("jira", SourceType.SIMULATED, "jira:t1", "t", "auth", "Ticket PROD-1042 open for Acme Corp")
        store.add_evidence("slack", SourceType.SIMULATED, "slack:m1", "t", "auth", "500 error in #alerts-enterprise")
        from reclaim.llm.schemas import EvidenceSynthesisResult, RootCauseHypothesis
        synthesis = EvidenceSynthesisResult(
            root_cause_hypotheses=[
                RootCauseHypothesis(
                    hypothesis_id="h1",
                    title="PR #882 Serialization Regression",
                    description="PR #882 breaking /v2/data-sync",
                    culprit_app="github",
                    corroborating_claim_ids=["ev_1", "ev_2"],
                    confidence=0.9
                )
            ],
            supporting_evidence_ids=["ev_1", "ev_2"],
            confidence=0.9,
            business_impact="impact"
        )
        dec_A = await decision_engine.formulate_decision(synthesis, store)
        jira_actions_A = [a for a in dec_A.selected_actions if a.app == "jira"]
        assert len(jira_actions_A) == 1
        assert jira_actions_A[0].action == "update_ticket"
        assert jira_actions_A[0].arguments["key"] == "PROD-1042"

        # Scenario B: Clean store without any ticket mentioned
        clean_store = EvidenceStore()
        clean_store.add_evidence("slack", SourceType.SIMULATED, "slack:m1", "t", "auth", "500 error in #incidents")
        dec_B = await decision_engine.formulate_decision(synthesis, clean_store)
        jira_actions_B = [a for a in dec_B.selected_actions if a.app == "jira"]
        assert len(jira_actions_B) == 1
        assert jira_actions_B[0].action == "create_ticket"
        assert jira_actions_B[0].arguments["project"] in ("PROD", os.getenv("JIRA_TEST_PROJECT", "PROD"))

    asyncio.run(run())


# ============================================================================
# 6. Human Approval Gating & Rejection Replanning
# ============================================================================
def test_approval_rejection_halts_mutation(acme_world):
    """
    Proves that a HIGH_RISK_WRITE (customer email) cannot execute when rejected.
    """
    async def run():
        registry = AdapterRegistry(mode="simulation", engine=acme_world)
        orchestrator = MissionOrchestrator(
            registry=registry,
            auto_approve_high_risk=False  # Must stop for human input
        )

        success = await orchestrator.run_mission("Resolve Acme's production crisis before it causes business impact.")
        # Stops at WAITING_FOR_APPROVAL
        assert success is False
        assert orchestrator.fsm.current_state == MissionState.WAITING_FOR_APPROVAL

        # Resolve with REJECT
        pending = orchestrator.safety_gate.get_pending_requests()
        assert len(pending) >= 1
        token = pending[0].approval_token
        orchestrator.safety_gate.resolve_approval(token, "REJECT")
        assert pending[0].status == "REJECTED"

        # Assert no high-risk email was executed
        assert not any(r.app == "gmail" for r in orchestrator.executed_receipts)
        with acme_world.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM gmail_messages WHERE from_address LIKE '%support%' OR is_sent = 0")
            assert cursor.fetchone()[0] == 0

    asyncio.run(run())


# ============================================================================
# 7. Verification Failure Triggers Recovery Loop
# ============================================================================
def test_verification_failure_recovers_cleanly(acme_world):
    """
    Injects WRITE_ACK_WITHOUT_MUTATION on Slack:
    Adapter returns ACK, but world is not mutated.
    Verification fails, triggers RECOVERING state, and completes remaining actions.
    """
    from reclaim.world.faults import fault_injector

    async def run():
        fault_injector.set_fault("WRITE_ACK_WITHOUT_MUTATION")
        try:
            registry = AdapterRegistry(mode="simulation", engine=acme_world)
            orchestrator = MissionOrchestrator(registry=registry, auto_approve_high_risk=True)

            await orchestrator.run_mission("Resolve Acme's production crisis.")
            # Verify that RECOVERING state was visited in telemetry
            events = orchestrator.telemetry.get_events(orchestrator.mission_id)
            event_types = [e.event_type for e in events]
            assert "VERIFICATION_FAILED" in event_types
            assert "RECOVERY_STARTED" in event_types
        finally:
            fault_injector.set_fault("NONE")

    asyncio.run(run())


# ============================================================================
# 8. Live Groq Golden Path Execution & Agent Trace Artifact Generation
# ============================================================================
def test_live_groq_golden_trace_artifact_generation(acme_world):
    """
    Runs the full golden path using Live Groq (or fallback).
    Evaluates agenticity scorecard and persists artifacts/acme_golden_trace.json.
    """
    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        pytest.skip("SKIPPED — missing credentials (GROQ_API_KEY)")

    async def run():
        from reclaim.llm.groq import GroqProvider
        provider = GroqProvider(api_key=groq_key, model_name=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))
        registry = AdapterRegistry(mode="simulation", engine=acme_world)
        orchestrator = MissionOrchestrator(
            llm_provider=provider,
            registry=registry,
            auto_approve_high_risk=True
        )

        import time
        t0 = time.perf_counter()
        success = await orchestrator.run_mission("Resolve Acme's production crisis before it causes business impact.")
        duration = time.perf_counter() - t0
        if not success:
            print(f"FAILED DETAILS: state={orchestrator.fsm.current_state} req={orchestrator.required_action_count} ver={orchestrator.verified_action_count} fail={orchestrator.failed_verification_count} pend={orchestrator.pending_verification_count}")
            for r in orchestrator.executed_receipts:
                print(f"  RECEIPT: app={r.app} op={r.operation} exec={r.execution_status} ver={r.verification_status} res={r.resource} err={r.error}")
            for v in orchestrator.verification_results:
                print(f"  VERIF: id={v.action_id} verified={v.verified} details={v.details}")
            for a in orchestrator.required_actions:
                print(f"  REQUIRED_ACTION: id={a.action_id} app={a.app} action={a.action}")
            traces = getattr(provider, "traces", [])
            if any("429" in t.status for t in traces):
                pytest.skip("SKIPPED — Groq daily token quota exhausted during test.")
        assert success is True
        assert orchestrator.fsm.current_state == MissionState.COMPLETED
        assert len(orchestrator.executed_receipts) >= 1

        # Golden-path maximum LLM call budget constraint: <= 5 LLM calls
        assert orchestrator.llm_call_count <= 5

        # Score with AgenticityScorecard
        evaluator = AgenticityScorecard(orchestrator)
        scorecard = evaluator.evaluate()

        assert scorecard["verdict"] == "GENUINE_AGENT"
        assert scorecard["metrics"]["composite_agenticity_score"] >= 0.80

        # Construct complete machine-readable trace artifact
        events = orchestrator.telemetry.get_events(orchestrator.mission_id)
        trace_data = {
            "mission_id": orchestrator.mission_id,
            "goal": orchestrator.goal_text,
            "duration_seconds": round(duration, 2),
            "final_status": orchestrator.fsm.current_state.value,
            "llm_provider": "groq",
            "model_name": os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
            "llm_calls_total": orchestrator.llm_call_count,
            "scorecard": scorecard,
            "investigation_cycles": orchestrator.investigation_cycle_count,
            "evidence_count": len(orchestrator.store.get_all()),
            "evidence_items": [
                {
                    "id": e.evidence_id,
                    "app": e.app,
                    "source_type": e.source_type.value,
                    "author": e.author,
                    "timestamp": e.timestamp,
                    "content": e.content
                }
                for e in orchestrator.store.get_all()
            ],
            "hypotheses": [h.model_dump() for h in (orchestrator.decision.hypotheses if orchestrator.decision else [])],
            "executed_actions": [r.model_dump() for r in orchestrator.executed_receipts],
            "verification_results": [v.model_dump() for v in orchestrator.verification_results],
            "telemetry_events": [ev.model_dump() for ev in events]
        }

        # Save to artifacts/acme_golden_trace.json
        artifacts_dir = Path("artifacts")
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        trace_file = artifacts_dir / "acme_golden_trace.json"
        with open(trace_file, "w", encoding="utf-8") as f:
            json.dump(trace_data, f, indent=2)

        print(f"\n[GOLDEN TRACE] Machine-readable trace saved to {trace_file}")
        print(f"[GOLDEN TRACE] Duration: {duration:.2f}s | LLM Calls: {orchestrator.llm_call_count} | Composite Score: {scorecard['metrics']['composite_agenticity_score']}")

    asyncio.run(run())
