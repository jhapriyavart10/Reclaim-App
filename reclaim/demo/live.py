"""
RECLAIM Live Mission Runner.
Executes the unified MissionOrchestrator against LIVE external systems (GitHub, Slack, Jira, Groq).
Enforces MISSION_DATA_POLICY=STRICT_LIVE with zero simulation leakage.
Produces artifacts/live_demo_trace.json for audit.
"""

import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from reclaim.demo.safety import validate_live_test_resources
from reclaim.demo.seed_live import seed_live_incident, MANIFEST_PATH
from reclaim.adapters.registry import AdapterRegistry
from reclaim.core.orchestrator import MissionOrchestrator, MissionDataPolicy
from reclaim.evaluation.scorecard import AgenticityScorecard
from reclaim.llm.groq import GroqProvider

TRACE_PATH = Path("artifacts/live_demo_trace.json")


def generate_unique_run_id() -> str:
    """Generate a collision-resistant unique run ID using UTC timestamp and UUID hex."""
    return f"RECLAIM-LIVE-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{uuid.uuid4().hex[:6]}"


async def run_live_mission(goal: str = None) -> dict:
    print(f"\n==================================================")
    print(f"       RECLAIM LIVE MISSION ORCHESTRATION")
    print(f"==================================================")

    # 1. Safety Check
    is_safe, code, details = validate_live_test_resources()
    if not is_safe:
        print(f"\n[SAFETY ERROR] {code}: {details.get('error')}")
        return {"status": code, "error": details.get("error")}

    # 2. Fresh Unique Run ID & Live Seeding
    run_id = generate_unique_run_id()
    print(f"Target Run ID: {run_id}")

    manifest = await seed_live_incident(run_id=run_id)
    run_dir = Path("artifacts/live_runs") / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / "seed.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    if not goal:
        goal = f"Investigate and resolve the current RECLAIM demo incident for run {run_id} across GitHub, Slack, and Jira."

    # 3. Setup Live Orchestrator (SAME CORE AGENT)
    groq_key = os.getenv("GROQ_API_KEY")
    if not groq_key:
        print(f"[ERROR] GROQ_API_KEY is not configured in .env.")
        return {"status": "ERROR_NO_GROQ_KEY"}

    provider = GroqProvider(
        api_key=groq_key,
        model_name=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
    )
    registry = AdapterRegistry(mode="live")

    orchestrator = MissionOrchestrator(
        llm_provider=provider,
        registry=registry,
        auto_approve_high_risk=True,
        policy=MissionDataPolicy.STRICT_LIVE
    )

    started_at = datetime.now(timezone.utc).isoformat()
    t0 = time.perf_counter()
    print(f"\n[ORCHESTRATOR START] Goal: '{goal}'")
    print(f"Policy: STRICT_LIVE (Only real GitHub, Slack, and Jira responses accepted)")
    
    success = await orchestrator.run_mission(goal)
    duration = time.perf_counter() - t0
    completed_at = datetime.now(timezone.utc).isoformat()
    print(f"\n[ORCHESTRATOR END] Success: {success} in {duration:.2f}s | State: {orchestrator.fsm.current_state.value}")

    # 4. Evaluate Scorecard
    evaluator = AgenticityScorecard(orchestrator)
    scorecard = evaluator.evaluate()

    # 5. Build Audit Trace
    events = orchestrator.telemetry.get_events(orchestrator.mission_id)
    all_evidence = orchestrator.store.get_all()
    live_evidence = [e for e in all_evidence if e.source_type.value == "LIVE"]
    simulated_evidence = [e for e in all_evidence if e.source_type.value == "SIMULATED"]

    trace = {
        "version": "2.0-live",
        "run_id": run_id,
        "mission_id": orchestrator.mission_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "goal": goal,
        "llm_provider": "groq",
        "model": os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
        "decision_source": getattr(orchestrator.decision, "decision_source", "none") if orchestrator.decision else "none",
        "reasoning_status": getattr(orchestrator.decision, "reasoning_status", "FAILED") if orchestrator.decision else "FAILED",
        "llm_traces": [t.model_dump() for t in getattr(provider, "traces", [])],
        "llm_call_count": orchestrator.llm_call_count,
        "duration_seconds": round(duration, 2),
        "policy": "STRICT_LIVE",
        "final_status": orchestrator.fsm.current_state.value,
        "live_apps": ["github", "slack", "jira"],
        "simulated_apps": [],
        "evidence_counts": {
            "total": len(all_evidence),
            "live": len(live_evidence),
            "simulated": len(simulated_evidence)
        },
        "action_counts": {
            "required": orchestrator.required_action_count,
            "verified": orchestrator.verified_action_count,
            "failed": orchestrator.failed_verification_count
        },
        "scorecard": scorecard,
        "capabilities": {
            k: v.model_dump() for k, v in (await registry.discover_capabilities()).items() if k in ["github", "slack", "jira"]
        },
        "live_evidence": [e.model_dump() for e in live_evidence],
        "hypotheses": [
            {
                "title": getattr(h, "title", "Hypothesis"),
                "root_cause": getattr(h, "root_cause", str(h)),
                "confidence": getattr(h, "confidence", 1.0),
                "supporting_evidence_ids": getattr(h, "supporting_evidence_ids", [])
            }
            for h in (getattr(orchestrator.plan, "hypotheses", []) if orchestrator.plan else [])
        ],
        "executed_actions": [
            {
                "app": r.app,
                "operation": r.operation,
                "resource": r.resource,
                "execution_status": r.execution_status,
                "verification_status": r.verification_status,
                "source_type": r.source_type.value,
                "error": r.error
            }
            for r in orchestrator.executed_receipts
        ],
        "verification_events": [v.model_dump() for v in orchestrator.verification_results],
        "telemetry_events": [evt.model_dump() for evt in events]
    }

    # Save immutable trace under run directory
    immutable_trace_path = run_dir / "trace.json"
    with open(immutable_trace_path, "w", encoding="utf-8") as f:
        json.dump(trace, f, indent=2)

    # Save convenience pointers
    TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TRACE_PATH, "w", encoding="utf-8") as f:
        json.dump(trace, f, indent=2)

    latest_path = Path("artifacts/latest_live_trace.json")
    with open(latest_path, "w", encoding="utf-8") as f:
        json.dump(trace, f, indent=2)

    print(f"\n[LIVE TRACE COMPLETE] Immutable trace written to {immutable_trace_path}")
    print(f"[LIVE TRACE COMPLETE] Pointer updated at {TRACE_PATH}")
    return trace


def main():
    try:
        res = asyncio.run(run_live_mission())
        if res.get("final_status") != "COMPLETED":
            print(f"[LIVE MISSION WARN] Final status is: {res.get('final_status')}")
    except Exception as e:
        print(f"[LIVE MISSION FATAL] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
