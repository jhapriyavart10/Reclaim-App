"""
RECLAIM Mission Control — Flask API Server.

Thin API layer over the existing MissionOrchestrator, AdapterRegistry,
TelemetryBus, EvidenceStore, and AgenticityScorecard.

Concurrency model:
- Flask runs in the main thread.
- Each mission executes in a single background thread via threading.Thread.
- The orchestrator's asyncio event loop is created and owned entirely by that thread.
- TelemetryBus (in-memory list) is the shared data structure.
  It is append-only during mission execution, read-only from Flask request handlers.
- Mission registry (dict) uses threading.Lock for safe registration.
- At most one mission may run concurrently (enforced by _active_mission_lock).
"""

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, Any, Optional, List

from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from dotenv import load_dotenv

load_dotenv()

from reclaim.adapters.registry import AdapterRegistry
from reclaim.adapters.base import SourceType, ConnectorStatus
from reclaim.core.orchestrator import MissionOrchestrator, MissionDataPolicy
from reclaim.core.state_machine import MissionState
from reclaim.core.telemetry import TelemetryBus, telemetry_bus
from reclaim.core.evidence_store import EvidenceStore
from reclaim.evaluation.scorecard import AgenticityScorecard
from reclaim.world.engine import WorldStateEngine
from reclaim.world.seed_acme import seed_acme_world

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ---------------------------------------------------------------------------
# Mission Registry — thread-safe storage of active/completed missions
# ---------------------------------------------------------------------------
_missions: Dict[str, Dict[str, Any]] = {}
_missions_lock = threading.Lock()
_active_mission_lock = threading.Lock()  # Only one mission at a time


def _register_mission(mission_id: str, orchestrator: MissionOrchestrator, mode: str, goal: str, scenario: str = "golden_path"):
    with _missions_lock:
        _missions[mission_id] = {
            "orchestrator": orchestrator,
            "mode": mode,
            "goal": goal,
            "scenario": scenario,
            "status": "running",
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "completed_at": None,
            "error": None,
            "thread": None,
        }


def _get_mission(mission_id: str) -> Optional[Dict[str, Any]]:
    with _missions_lock:
        return _missions.get(mission_id)


def _safe_evidence_to_dict(ev) -> dict:
    """Convert evidence item to dict, filtering secrets."""
    d = ev.model_dump()
    # Remove raw_data fields that might contain auth info
    raw = d.get("raw_data", {})
    for key in list(raw.keys()):
        kl = key.lower()
        if any(s in kl for s in ["token", "key", "auth", "secret", "password", "credential"]):
            raw[key] = "[REDACTED]"
    return d


def _safe_receipt_to_dict(receipt) -> dict:
    """Convert action receipt to safe dict."""
    d = receipt.model_dump()
    d["source_type"] = receipt.source_type.value if hasattr(receipt.source_type, "value") else str(receipt.source_type)
    return d


# ---------------------------------------------------------------------------
# Background mission execution
# ---------------------------------------------------------------------------
def _run_mission_thread(mission_id: str, orchestrator: MissionOrchestrator, goal: str, mode: str, scenario: str):
    """
    Runs the mission orchestrator in a dedicated thread with its own asyncio event loop.
    The thread owns the loop exclusively — no cross-thread loop sharing.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        success = loop.run_until_complete(orchestrator.run_mission(goal))
        with _missions_lock:
            entry = _missions.get(mission_id)
            if entry:
                entry["status"] = "completed" if success else "failed"
                entry["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    except Exception as e:
        logger.error(f"Mission {mission_id} failed: {e}", exc_info=True)
        with _missions_lock:
            entry = _missions.get(mission_id)
            if entry:
                entry["status"] = "failed"
                entry["error"] = str(e)
                entry["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    finally:
        loop.close()
        _active_mission_lock.release()


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------

@app.route("/api/health", methods=["GET"])
def health():
    groq_key = os.getenv("GROQ_API_KEY", "")
    return jsonify({
        "status": "ok",
        "llm": {
            "provider": "groq",
            "model": os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
            "configured": bool(groq_key),
        },
        "adapters": {
            "github": bool(os.getenv("GITHUB_TOKEN")),
            "slack": bool(os.getenv("SLACK_BOT_TOKEN")),
            "jira": bool(os.getenv("JIRA_API_TOKEN")),
        },
    })


@app.route("/api/capabilities", methods=["GET"])
def capabilities():
    """Return live/simulated adapter status for each app."""
    loop = asyncio.new_event_loop()
    try:
        registry = AdapterRegistry(mode="hybrid")
        caps = loop.run_until_complete(registry.get_all_capabilities())
        result = {}
        for app_name, cap in caps.items():
            d = cap.model_dump()
            d["source_type"] = cap.source_type.value
            d["connector_status"] = cap.connector_status.value if hasattr(cap.connector_status, "value") else str(cap.connector_status)
            result[app_name] = d
        # Add Groq status
        result["groq"] = {
            "app": "groq",
            "source_type": "LIVE" if os.getenv("GROQ_API_KEY") else "UNAVAILABLE",
            "availability": "AVAILABLE" if os.getenv("GROQ_API_KEY") else "UNAVAILABLE",
            "authentication_status": "AUTHENTICATED" if os.getenv("GROQ_API_KEY") else "UNCONFIGURED",
            "model": os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
        }
        return jsonify(result)
    finally:
        loop.close()


@app.route("/api/missions", methods=["POST"])
def create_mission():
    """Start a new mission. Returns immediately with mission_id."""
    data = request.get_json(silent=True) or {}
    goal = data.get("goal", "Resolve Acme's production crisis before it causes business impact.")
    mode = data.get("mode", "simulation")  # "live" or "simulation"
    scenario = data.get("scenario", "golden_path")

    # Prevent concurrent missions
    acquired = _active_mission_lock.acquire(blocking=False)
    if not acquired:
        return jsonify({"error": "A mission is already running. Wait for it to complete."}), 409

    try:
        # Clear global telemetry for fresh mission
        telemetry_bus.clear()

        if mode == "live":
            groq_key = os.getenv("GROQ_API_KEY")
            if not groq_key:
                _active_mission_lock.release()
                return jsonify({"error": "GROQ_API_KEY not configured for live mode."}), 400

            from reclaim.llm.groq import GroqProvider
            provider = GroqProvider(api_key=groq_key, model_name=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))
            registry = AdapterRegistry(mode="live")
            policy = MissionDataPolicy.STRICT_LIVE

            # For live mode, auto-seed if needed
            from reclaim.demo.seed_live import MANIFEST_PATH
            if not MANIFEST_PATH.exists() or json.loads(MANIFEST_PATH.read_text()).get("cleanup_completed", False):
                from reclaim.demo.seed_live import seed_live_incident
                seed_loop = asyncio.new_event_loop()
                try:
                    manifest = seed_loop.run_until_complete(seed_live_incident())
                finally:
                    seed_loop.close()
                run_id = manifest.get("run_id", "")
                if not goal or "Acme" in goal:
                    goal = f"Investigate and resolve the current RECLAIM demo incident for run {run_id} across GitHub, Slack, and Jira."
        else:
            # Simulation mode
            groq_key = os.getenv("GROQ_API_KEY")
            if groq_key:
                from reclaim.llm.groq import GroqProvider
                provider = GroqProvider(api_key=groq_key, model_name=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))
            else:
                from reclaim.llm.mock import MockLLMProvider
                provider = MockLLMProvider()

            engine = WorldStateEngine()
            seed_acme_world(engine)

            # Apply scenario-specific fault injection
            if scenario == "verification_failure":
                from reclaim.adapters.base import ConnectorStatus as CS
                from reclaim.world.fault_injector import fault_injector
                fault_injector.set_fault("WRITE_ACK_WITHOUT_MUTATION")
            elif scenario == "slack_outage":
                from reclaim.world.fault_injector import fault_injector
                fault_injector.set_fault("HTTP_503")

            registry = AdapterRegistry(mode="simulation", engine=engine)
            policy = MissionDataPolicy.SIMULATION

        orchestrator = MissionOrchestrator(
            llm_provider=provider,
            registry=registry,
            auto_approve_high_risk=(scenario not in ["approval_rejected"]),
            policy=policy,
        )
        mission_id = orchestrator.mission_id

        _register_mission(mission_id, orchestrator, mode, goal, scenario)

        # Launch background thread — it will release _active_mission_lock when done
        t = threading.Thread(
            target=_run_mission_thread,
            args=(mission_id, orchestrator, goal, mode, scenario),
            daemon=True,
        )
        with _missions_lock:
            _missions[mission_id]["thread"] = t
        t.start()

        return jsonify({
            "mission_id": mission_id,
            "mode": mode,
            "goal": goal,
            "scenario": scenario,
            "status": "running",
        }), 201

    except Exception as e:
        _active_mission_lock.release()
        logger.error(f"Failed to create mission: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500


@app.route("/api/missions/<mission_id>", methods=["GET"])
def get_mission(mission_id: str):
    """Get current mission state snapshot."""
    entry = _get_mission(mission_id)
    if not entry:
        return jsonify({"error": "Mission not found"}), 404

    o = entry["orchestrator"]
    evidence = o.store.get_all()
    live_ev = [e for e in evidence if e.source_type == SourceType.LIVE]
    sim_ev = [e for e in evidence if e.source_type == SourceType.SIMULATED]

    # Build hypothesis info
    hypotheses = []
    if o.decision and hasattr(o.decision, "hypotheses") and o.decision.hypotheses:
        for h in o.decision.hypotheses:
            hypotheses.append({
                "hypothesis_id": getattr(h, "hypothesis_id", "h1"),
                "title": getattr(h, "title", "Unknown"),
                "description": getattr(h, "description", ""),
                "confidence": getattr(h, "confidence", 0.0),
                "culprit_app": getattr(h, "culprit_app", ""),
                "corroborating_claim_ids": getattr(h, "corroborating_claim_ids", []),
            })

    return jsonify({
        "mission_id": mission_id,
        "mode": entry["mode"],
        "goal": entry["goal"],
        "scenario": entry["scenario"],
        "status": entry["status"],
        "started_at": entry["started_at"],
        "completed_at": entry["completed_at"],
        "error": entry["error"],
        "current_state": o.fsm.current_state.value,
        "state_history": [(s.value, r) for s, r in o.fsm.get_history()],
        "policy": o.policy.value,
        "evidence_counts": {
            "total": len(evidence),
            "live": len(live_ev),
            "simulated": len(sim_ev),
        },
        "action_counts": {
            "required": o.required_action_count,
            "verified": o.verified_action_count,
            "failed": o.failed_verification_count,
            "pending": o.pending_verification_count,
        },
        "llm_call_count": o.llm_call_count,
        "hypotheses": hypotheses,
    })


@app.route("/api/missions/<mission_id>/events", methods=["GET"])
def mission_events_sse(mission_id: str):
    """SSE stream of telemetry events for a mission."""
    entry = _get_mission(mission_id)
    if not entry:
        return jsonify({"error": "Mission not found"}), 404

    def generate():
        last_index = 0
        seen_ids = set()
        while True:
            events = telemetry_bus.get_events(mission_id)
            new_events = events[last_index:]
            for evt in new_events:
                if evt.event_id not in seen_ids:
                    seen_ids.add(evt.event_id)
                    data = json.dumps(evt.model_dump(), default=str)
                    yield f"id: {evt.event_id}\nevent: telemetry\ndata: {data}\n\n"
            last_index = len(events)

            # Check if mission is done
            with _missions_lock:
                e = _missions.get(mission_id)
                if e and e["status"] in ("completed", "failed"):
                    # Send final state event
                    o = e["orchestrator"]
                    final = {
                        "event_type": "MISSION_FINAL_STATE",
                        "mission_id": mission_id,
                        "status": e["status"],
                        "current_state": o.fsm.current_state.value,
                    }
                    yield f"id: final\nevent: mission_end\ndata: {json.dumps(final)}\n\n"
                    break

            time.sleep(0.3)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.route("/api/missions/<mission_id>/evidence", methods=["GET"])
def mission_evidence(mission_id: str):
    entry = _get_mission(mission_id)
    if not entry:
        return jsonify({"error": "Mission not found"}), 404

    o = entry["orchestrator"]
    items = [_safe_evidence_to_dict(ev) for ev in o.store.get_all()]
    return jsonify({"evidence": items, "count": len(items)})


@app.route("/api/missions/<mission_id>/actions", methods=["GET"])
def mission_actions(mission_id: str):
    entry = _get_mission(mission_id)
    if not entry:
        return jsonify({"error": "Mission not found"}), 404

    o = entry["orchestrator"]
    receipts = [_safe_receipt_to_dict(r) for r in o.executed_receipts]
    verifications = [v.model_dump() for v in o.verification_results]

    # Include proposed actions from decision
    proposed = []
    if o.decision and hasattr(o.decision, "selected_actions"):
        for a in o.decision.selected_actions:
            proposed.append({
                "action_id": a.action_id,
                "app": a.app,
                "action": getattr(a, "action", ""),
                "arguments": a.arguments if hasattr(a, "arguments") else {},
                "risk_level": a.risk_level,
                "requires_approval": a.requires_approval,
                "rationale": getattr(a, "rationale", ""),
                "expected_effect": getattr(a, "expected_effect", {}),
            })

    return jsonify({
        "proposed_actions": proposed,
        "executed_receipts": receipts,
        "verifications": verifications,
    })


@app.route("/api/missions/<mission_id>/scorecard", methods=["GET"])
def mission_scorecard(mission_id: str):
    entry = _get_mission(mission_id)
    if not entry:
        return jsonify({"error": "Mission not found"}), 404

    o = entry["orchestrator"]
    try:
        evaluator = AgenticityScorecard(o)
        scorecard = evaluator.evaluate()
        return jsonify(scorecard)
    except Exception as e:
        return jsonify({"error": f"Scorecard evaluation failed: {e}"}), 500


@app.route("/api/missions/<mission_id>/approval", methods=["POST"])
def mission_approval(mission_id: str):
    entry = _get_mission(mission_id)
    if not entry:
        return jsonify({"error": "Mission not found"}), 404

    data = request.get_json(silent=True) or {}
    action = data.get("action", "APPROVE")  # APPROVE or REJECT
    approval_token = data.get("approval_token")

    if not approval_token:
        return jsonify({"error": "approval_token is required"}), 400

    o = entry["orchestrator"]
    try:
        o.safety_gate.resolve_approval(approval_token, action)
        return jsonify({"status": "resolved", "action": action})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main():
    port = int(os.getenv("RECLAIM_API_PORT", "5000"))
    print(f"\n{'='*60}")
    print(f"  RECLAIM Mission Control API Server")
    print(f"  http://localhost:{port}")
    print(f"{'='*60}")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
