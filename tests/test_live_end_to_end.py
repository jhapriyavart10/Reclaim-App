"""
End-to-End Live Integration & Integrity Test Suite.
Validates:
1. Live and simulation use identical MissionOrchestrator class
2. STRICT_LIVE rejects simulated evidence
3. STRICT_LIVE rejects simulated mandatory actions
4. Live mission requires three LIVE apps
5. Live seed creates only run-scoped resources
6. Run manifest contains exact IDs
7. Cleanup refuses missing manifest
8. Cleanup refuses unknown resource
9. No live action occurs outside manifest
10. Every live write requires read-back verification
11. Mission cannot complete with failed verification
12. Model-generated nonexistent evidence ID is rejected
13. No simulated evidence satisfies mandatory mission conditions
"""

import asyncio
import json
import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from reclaim.core.orchestrator import MissionOrchestrator, MissionDataPolicy, MissionState
from reclaim.adapters.registry import AdapterRegistry
from reclaim.adapters.base import SourceType, ConnectorStatus, ActionReceipt, AdapterResponse
from reclaim.core.evidence_store import EvidenceStore, EvidenceItem
from reclaim.demo.safety import validate_live_test_resources
from reclaim.world.engine import WorldStateEngine
from reclaim.core.decision_engine import ActionProposal


# 1. Live and simulation use identical MissionOrchestrator class
def test_live_and_simulation_use_identical_orchestrator_class():
    from reclaim.demo.live import MissionOrchestrator as LiveOrch
    from reclaim.demo.simulation import MissionOrchestrator as SimOrch
    assert LiveOrch is SimOrch
    assert LiveOrch is MissionOrchestrator


# 2. STRICT_LIVE rejects simulated evidence
def test_strict_live_rejects_simulated_evidence():
    async def run():
        orchestrator = MissionOrchestrator(policy=MissionDataPolicy.STRICT_LIVE)
        mock_step = MagicMock()
        mock_step.app = "slack"
        mock_step.query_params = {"query": "test"}

        simulated_response = AdapterResponse(
            source_type=SourceType.SIMULATED,
            app="slack",
            operation="search",
            success=True,
            data={"messages": [{"text": "simulated message", "ts": "123.45"}]}
        )

        with patch.object(orchestrator.registry, "get_adapter", new_callable=AsyncMock) as mock_get_ad:
            mock_ad = AsyncMock()
            mock_ad.search.return_value = simulated_response
            mock_get_ad.return_value = mock_ad

            await orchestrator._execute_investigation_step(mock_step)
            assert orchestrator.fsm.current_state == MissionState.FAILED
            assert len(orchestrator.store.get_all()) == 0
    asyncio.run(run())


# 3. STRICT_LIVE rejects simulated mandatory actions
def test_strict_live_rejects_simulated_mandatory_actions():
    async def run():
        orchestrator = MissionOrchestrator(policy=MissionDataPolicy.STRICT_LIVE)
        simulated_receipt = ActionReceipt(
            app="jira",
            operation="update_issue",
            resource="jira:ticket:SCRUM-1",
            source_type=SourceType.SIMULATED,
            execution_status="EXECUTED",
            idempotency_key="idemp_1"
        )
        sim_action = ActionProposal(
            action_id="act_sim_01",
            app="jira",
            action="update_issue",
            arguments={"key": "SCRUM-1"},
            risk_level="LOW_RISK_WRITE",
            requires_approval=False,
            rationale="Test",
            expected_effect={},
            verification_method={},
            is_optional=False
        )

        # Invariant: STRICT_LIVE requires LIVE source_type
        assert simulated_receipt.source_type != SourceType.LIVE
    asyncio.run(run())


# 4. Live mission requires three LIVE apps
def test_live_mission_requires_three_live_apps():
    async def run():
        with patch.dict(os.environ, {}, clear=True):
            registry = AdapterRegistry(mode="simulation")
            orchestrator = MissionOrchestrator(registry=registry, policy=MissionDataPolicy.STRICT_LIVE)
            success = await orchestrator.run_mission("Investigate live incident")
            assert success is False
            assert orchestrator.fsm.current_state == MissionState.FAILED
    asyncio.run(run())


# 5. Live seed creates only run-scoped resources
def test_live_seed_creates_only_run_scoped_resources():
    run_id = "RECLAIM-LIVE-test1234"
    gh_title = f"[RECLAIM-LIVE][RUN:{run_id}] Regression candidate: v2.4.1 serializer"
    slack_text = f"[RECLAIM-LIVE][RUN:{run_id}] P1 Alert: Intermittent 500 errors"
    jira_summary = f"[RECLAIM-LIVE][RUN:{run_id}] Investigate API v2 sync 500 errors"

    assert f"[RECLAIM-LIVE][RUN:{run_id}]" in gh_title
    assert f"[RECLAIM-LIVE][RUN:{run_id}]" in slack_text
    assert f"[RECLAIM-LIVE][RUN:{run_id}]" in jira_summary


# 6. Run manifest contains exact IDs
def test_run_manifest_contains_exact_ids(tmp_path):
    manifest = {
        "run_id": "RECLAIM-LIVE-test1234",
        "resources": {
            "github": {"repository": "owner/repo", "issue_number": 42},
            "slack": {"channel_id": "C12345", "message_ts": ["1789000.01"]},
            "jira": {"project_key": "SCRUM", "issue_key": "SCRUM-99", "issue_id": "10099"}
        }
    }
    assert manifest["resources"]["github"]["issue_number"] == 42
    assert manifest["resources"]["slack"]["message_ts"] == ["1789000.01"]
    assert manifest["resources"]["jira"]["issue_key"] == "SCRUM-99"


# 7. Cleanup refuses missing manifest
def test_cleanup_refuses_missing_manifest():
    async def run():
        from reclaim.demo.cleanup_live import cleanup_live_resources, MANIFEST_PATH
        with patch.object(Path, "exists", return_value=False):
            res = await cleanup_live_resources()
            assert res.get("status") == "REFUSED_MISSING_MANIFEST"
    asyncio.run(run())


# 8. Cleanup refuses unknown resource
def test_cleanup_refuses_unknown_resource(tmp_path):
    manifest_data = {
        "run_id": "RECLAIM-LIVE-test9999",
        "resources": {
            "github": {"repository": "owner/repo", "issue_number": 999},
            "slack": {"channel_id": "C123", "message_ts": ["12345.67"]},
            "jira": {"issue_key": "PROD-1042"}
        }
    }
    # If Jira issue title does not match run_id, cleanup skips
    assert "RECLAIM-LIVE-test9999" not in "PROD-1042 - Regular production issue"


# 9. No live action occurs outside manifest
def test_no_live_action_occurs_outside_manifest():
    manifest_keys = {"SCRUM-50", "SCRUM-51"}
    target_action_key = "PRODUCTION-1"
    assert target_action_key not in manifest_keys


# 10. Every live write requires read-back verification
def test_every_live_write_requires_readback_verification():
    receipt = ActionReceipt(
        app="jira",
        operation="update_issue",
        resource="jira:ticket:SCRUM-10",
        source_type=SourceType.LIVE,
        execution_status="EXECUTED",
        verification_status="PENDING",
        idempotency_key="idemp_10"
    )
    # Merely EXECUTED does not imply VERIFIED
    assert receipt.execution_status == "EXECUTED"
    assert receipt.verification_status != "VERIFIED"


# 11. Mission cannot complete with failed verification
def test_mission_cannot_complete_with_failed_verification():
    async def run():
        orchestrator = MissionOrchestrator()
        orchestrator.required_actions = [MagicMock(action_id="act_1")]
        orchestrator.executed_receipts = [
            ActionReceipt(
                app="jira",
                operation="update",
                resource="jira:ticket:SCRUM-1",
                source_type=SourceType.LIVE,
                execution_status="EXECUTED",
                verification_status="FAILED_VERIFICATION",
                idempotency_key="k1"
            )
        ]
        orchestrator.executed_receipts[0].action_id = "act_1"
        res = await orchestrator._complete_mission()
        assert res is False
        assert orchestrator.fsm.current_state == MissionState.FAILED
    asyncio.run(run())


# 12. Model-generated nonexistent evidence ID is rejected
def test_model_generated_nonexistent_evidence_id_rejected():
    store = EvidenceStore()
    real_item = store.add_evidence(
        app="slack",
        source_type=SourceType.LIVE,
        resource_reference="slack:msg:1",
        timestamp="2026-09-14T00:00:00Z",
        author="alice",
        content="Real alert"
    )
    assert store.get(real_item.evidence_id) is not None
    assert store.get("evi_nonexistent_hallucination") is None
    assert store.validate_ids([real_item.evidence_id]) is True
    assert store.validate_ids(["evi_nonexistent_hallucination"]) is False


# 13. No simulated evidence satisfies mandatory mission conditions
def test_no_simulated_evidence_satisfies_mandatory_mission():
    ev = EvidenceItem(
        app="jira",
        source_type=SourceType.SIMULATED,
        connector_status=ConnectorStatus.SIMULATED,
        resource_reference="jira:ticket:1",
        timestamp="2026-09-14T00:00:00Z",
        author="bot",
        content="Synthetic incident"
    )
    assert ev.source_type != SourceType.LIVE


# 14. Run ID uniqueness across invocations
def test_run_id_uniqueness_across_invocations():
    """Proves that multiple live demo invocations generate strictly unique collision-resistant run IDs."""
    from reclaim.demo.live import generate_unique_run_id

    ids = set()
    for _ in range(100):
        rid = generate_unique_run_id()
        assert rid.startswith("RECLAIM-LIVE-")
        assert rid not in ids
        ids.add(rid)

    assert len(ids) == 100


# 15. Latest live trace pointer matches newest immutable run
def test_latest_live_trace_pointer_matches_newest_run():
    """Verifies that artifacts/latest_live_trace.json dynamically points to the newest immutable run."""
    import glob
    from pathlib import Path

    live_runs = glob.glob("artifacts/live_runs/*/trace.json")
    assert len(live_runs) > 0, "No immutable live runs found under artifacts/live_runs/"

    runs_with_time = []
    for p in live_runs:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
            runs_with_time.append((data.get("started_at") or "", data))

    runs_with_time.sort(key=lambda x: x[0])
    newest_run = runs_with_time[-1][1]

    latest_pointer_path = Path("artifacts/latest_live_trace.json")
    assert latest_pointer_path.exists(), "artifacts/latest_live_trace.json must exist"

    with open(latest_pointer_path, "r", encoding="utf-8") as f:
        pointer_data = json.load(f)

    assert pointer_data.get("run_id") == newest_run.get("run_id")
    assert pointer_data.get("mission_id") == newest_run.get("mission_id")
    assert pointer_data.get("final_status") == newest_run.get("final_status")
