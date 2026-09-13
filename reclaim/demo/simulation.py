"""
RECLAIM Simulation Demo Runner.
Executes the EXACT SAME MissionOrchestrator in SIMULATION mode against the WorldStateEngine.
Proves that the agentic core (planner, synthesizer, decision engine, safety gate, verification loop)
is unified across both simulated and live environments.
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from reclaim.world.engine import WorldStateEngine
from reclaim.world.seed_acme import seed_acme_world
from reclaim.adapters.registry import AdapterRegistry
from reclaim.core.orchestrator import MissionOrchestrator, MissionDataPolicy
from reclaim.evaluation.scorecard import AgenticityScorecard
from reclaim.llm.groq import GroqProvider

TRACE_PATH = Path("artifacts/simulation_demo_trace.json")


async def run_simulation_mission(goal: str = None) -> dict:
    if not goal:
        goal = "Resolve Acme's production crisis before it causes business impact."

    print(f"\n==================================================")
    print(f"    RECLAIM SIMULATION PARITY MISSION RUNNER")
    print(f"==================================================")
    print(f"Goal: '{goal}'")
    print(f"Policy: SIMULATION (Deterministic synthetic world engine)")

    # 1. Initialize World State & Registry
    engine = WorldStateEngine()
    seed_acme_world(engine)
    registry = AdapterRegistry(mode="simulation", engine=engine)

    # 2. Setup Provider
    groq_key = os.getenv("GROQ_API_KEY")
    if groq_key:
        provider = GroqProvider(api_key=groq_key, model_name=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))
    else:
        from reclaim.llm.mock import MockLLMProvider
        provider = MockLLMProvider()

    # 3. Instantiate EXACT SAME MissionOrchestrator
    orchestrator = MissionOrchestrator(
        llm_provider=provider,
        registry=registry,
        auto_approve_high_risk=True,
        policy=MissionDataPolicy.SIMULATION
    )

    t0 = time.perf_counter()
    success = await orchestrator.run_mission(goal)
    duration = time.perf_counter() - t0

    print(f"\n[SIMULATION END] Success: {success} in {duration:.2f}s | State: {orchestrator.fsm.current_state.value}")

    # 4. Evaluate
    evaluator = AgenticityScorecard(orchestrator)
    scorecard = evaluator.evaluate()

    trace = {
        "mission_id": orchestrator.mission_id,
        "goal": goal,
        "mode": "simulation",
        "duration_seconds": round(duration, 2),
        "final_status": orchestrator.fsm.current_state.value,
        "scorecard": scorecard,
        "actions_verified": orchestrator.verified_action_count,
        "actions_required": orchestrator.required_action_count,
        "orchestrator_class": orchestrator.__class__.__name__
    }

    TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(TRACE_PATH, "w", encoding="utf-8") as f:
        json.dump(trace, f, indent=2)

    print(f"[SIMULATION TRACE] Saved to {TRACE_PATH}")
    return trace


def main():
    asyncio.run(run_simulation_mission())


if __name__ == "__main__":
    main()
