"""
Script to execute the complete simulated golden path and generate artifacts/acme_golden_trace_v2.json.
Enforces deterministic invariants:
  required_action_count == verified_action_count
  failed_verification_count == 0
  pending_verification_count == 0
  verification_rate == 1.0
"""

import asyncio
import json
import os
import time
from pathlib import Path

from reclaim.world.engine import WorldStateEngine
from reclaim.world.seed_acme import seed_acme_world
from reclaim.adapters.registry import AdapterRegistry
from reclaim.core.orchestrator import MissionOrchestrator, MissionDataPolicy
from reclaim.core.state_machine import MissionState
from reclaim.evaluation.scorecard import AgenticityScorecard
from reclaim.llm.groq import GroqProvider


async def main():
    print("[GOLDEN TRACE V2] Initializing canonical Acme Corp world...")
    world_db = "artifacts/acme_golden_v2.db"
    Path("artifacts").mkdir(parents=True, exist_ok=True)
    if os.path.exists(world_db):
        os.remove(world_db)

    engine = WorldStateEngine(db_path=world_db)
    seed_acme_world(engine)

    groq_key = os.getenv("GROQ_API_KEY")
    provider = None
    if groq_key:
        print("[GOLDEN TRACE V2] Configuring Live Groq Provider (openai/gpt-oss-120b)...")
        provider = GroqProvider(api_key=groq_key, model_name=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))

    registry = AdapterRegistry(mode="simulation", engine=engine)
    orchestrator = MissionOrchestrator(
        llm_provider=provider,
        registry=registry,
        auto_approve_high_risk=True,
        policy=MissionDataPolicy.SIMULATION
    )

    t0 = time.perf_counter()
    print("[GOLDEN TRACE V2] Running mission: 'Resolve Acme\\'s production crisis before it causes business impact.'...")
    success = await orchestrator.run_mission("Resolve Acme's production crisis before it causes business impact.")
    duration = time.perf_counter() - t0

    print(f"[GOLDEN TRACE V2] Mission execution finished in {duration:.2f}s with success={success}")
    print(f"[GOLDEN TRACE V2] Final State: {orchestrator.fsm.current_state.value}")
    print(f"[GOLDEN TRACE V2] Required Actions: {orchestrator.required_action_count}")
    print(f"[GOLDEN TRACE V2] Verified Actions: {orchestrator.verified_action_count}")
    print(f"[GOLDEN TRACE V2] Failed Verifications: {orchestrator.failed_verification_count}")
    print(f"[GOLDEN TRACE V2] Pending Verifications: {orchestrator.pending_verification_count}")

    print("\n--- EXECUTED RECEIPTS ---")
    for r in orchestrator.executed_receipts:
        print(f"Receipt: app={r.app} op={r.operation} res={r.resource} exec={r.execution_status} ver={r.verification_status} err={r.error}")

    print("\n--- VERIFICATION RESULTS ---")
    for v in orchestrator.verification_results:
        print(f"Verification: action_id={v.action_id} verified={v.verified} details={v.details}")

    # Check invariants
    assert success is True, "Mission must succeed"
    assert orchestrator.fsm.current_state == MissionState.COMPLETED, "State must be COMPLETED"
    assert orchestrator.failed_verification_count == 0, "No verification failures allowed"
    assert orchestrator.pending_verification_count == 0, "No pending verifications allowed"
    assert orchestrator.required_action_count == orchestrator.verified_action_count, "All required actions must be verified"

    # Evaluate Scorecard
    evaluator = AgenticityScorecard(orchestrator)
    scorecard = evaluator.evaluate()
    verification_rate = scorecard["metrics"]["verification_rate"]
    print(f"[GOLDEN TRACE V2] Verification Rate: {verification_rate}")
    print(f"[GOLDEN TRACE V2] Composite Score: {scorecard['metrics']['composite_agenticity_score']}")
    print(f"[GOLDEN TRACE V2] Scorecard Verdict: {scorecard['verdict']}")

    assert verification_rate == 1.0, f"Expected verification_rate == 1.0, got {verification_rate}"
    assert scorecard["verdict"] == "GENUINE_AGENT", f"Expected GENUINE_AGENT, got {scorecard['verdict']}"

    # Build comprehensive trace artifact
    events = orchestrator.telemetry.get_events(orchestrator.mission_id)
    provenance = orchestrator.get_evidence_provenance_report()

    # Map all actions with their execution & verification status and evidence
    actions_summary = []
    for prop in orchestrator.required_actions:
        receipt = next((r for r in orchestrator.executed_receipts if getattr(r, "action_id", None) == prop.action_id or prop.action_id in getattr(r, "idempotency_key", "")), None)
        v_result = next((v for v in orchestrator.verification_results if v.action_id == prop.action_id), None)
        actions_summary.append({
            "action_id": prop.action_id,
            "app": prop.app,
            "action": getattr(prop, "action", getattr(prop, "operation", "action")),
            "arguments": getattr(prop, "arguments", getattr(prop, "parameters", {})),
            "risk_level": prop.risk_level,
            "is_optional": getattr(prop, "is_optional", False),
            "action_status": receipt.execution_status if receipt else "UNEXECUTED",
            "receipt_resource": receipt.resource if receipt else None,
            "verification_status": receipt.verification_status if receipt else "UNVERIFIED",
            "verification_evidence": {
                "verified": v_result.verified if v_result else False,
                "verification_method": v_result.verification_method if v_result else None,
                "observed_state": v_result.observed_state if v_result else {},
                "details": v_result.details if v_result else None
            }
        })

    trace_data = {
        "version": "2.0",
        "mission_id": orchestrator.mission_id,
        "goal": orchestrator.goal_text,
        "duration_seconds": round(duration, 2),
        "final_status": orchestrator.fsm.current_state.value,
        "policy": orchestrator.policy.value,
        "llm_provider": "groq" if provider else "deterministic_engine",
        "model_name": os.getenv("GROQ_MODEL", "openai/gpt-oss-120b") if provider else "rule_based_engine",
        "llm_calls_total": orchestrator.llm_call_count,
        "final_completion_condition": {
            "required_action_count": orchestrator.required_action_count,
            "verified_action_count": orchestrator.verified_action_count,
            "failed_verification_count": orchestrator.failed_verification_count,
            "pending_verification_count": orchestrator.pending_verification_count,
            "is_fully_verified": True,
            "completion_invariant_satisfied": True
        },
        "scorecard": scorecard,
        "all_actions": actions_summary,
        "evidence_provenance": provenance,
        "evidence_count": len(orchestrator.store.get_all()),
        "evidence_items": [
            {
                "id": e.evidence_id,
                "app": e.app,
                "source_type": e.source_type.value,
                "connector_status": e.connector_status.value if hasattr(e.connector_status, "value") else str(e.connector_status),
                "author": e.author,
                "timestamp": e.timestamp,
                "resource_reference": e.resource_reference,
                "content": e.content
            }
            for e in orchestrator.store.get_all()
        ],
        "hypotheses": [h.model_dump() for h in (orchestrator.decision.hypotheses if orchestrator.decision else [])],
        "executed_receipts": [r.model_dump() for r in orchestrator.executed_receipts],
        "verification_results": [v.model_dump() for v in orchestrator.verification_results],
        "telemetry_events": [ev.model_dump() for ev in events]
    }

    out_file = Path("artifacts/acme_golden_trace_v2.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(trace_data, f, indent=2)

    print(f"\n[SUCCESS] Emitted golden trace artifact: {out_file.resolve()}")
    print(f"[SUCCESS] Verification Rate = {trace_data['scorecard']['metrics']['verification_rate']}")
    print(f"[SUCCESS] Completion Condition Invariant Satisfied: True")


if __name__ == "__main__":
    asyncio.run(main())
