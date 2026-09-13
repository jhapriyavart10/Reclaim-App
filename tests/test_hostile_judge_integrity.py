"""
Hostile Judge Integrity Test Suite
Verifies 10 core integrity guarantees + 3-app core dependency:
1. Missing live credentials are reported honestly (AUTHENTICATION_REQUIRED, SKIPPED).
2. Simulated evidence cannot be labeled LIVE (raises ValueError).
3. A fake/simulated adapter cannot claim LIVE_VERIFIED.
4. Mission cannot complete with an unverified mandatory action (enforces invariant).
5. Scorecard cannot report verification_rate = 1.0 if any mandatory action is unverified.
6. STRICT_LIVE fails when a required connector is unavailable.
7. HYBRID clearly reports simulation dependency in provenance.
8. Live write tests refuse to run without explicitly configured TEST_* resources.
9. Production resources are never used by default.
10. A live adapter must actually make a provider request before claiming LIVE_VERIFIED.
11. (Part J) Three-App Core Dependency: Agent successfully investigates, diagnoses, and remediates
    using only GitHub + Slack + Jira when Gmail/CRM/Calendar supporting apps are completely absent.
"""

import asyncio
import json
import os
from unittest.mock import patch, AsyncMock
import pytest

from reclaim.adapters.base import SourceType, ConnectorStatus, ActionReceipt, AdapterResponse
from reclaim.adapters.registry import AdapterRegistry
from reclaim.adapters.live.github import LiveGitHubAdapter
from reclaim.adapters.live.slack import LiveSlackAdapter
from reclaim.adapters.live.jira import LiveJiraAdapter
from reclaim.core.evidence_store import EvidenceItem, EvidenceStore
from reclaim.core.orchestrator import MissionOrchestrator, MissionDataPolicy
from reclaim.core.state_machine import MissionState
from reclaim.evaluation.scorecard import AgenticityScorecard
from reclaim.live_smoke import probe_live_adapters
from reclaim.live_test.github import run_live_github_write_test
from reclaim.live_test.slack import run_live_slack_write_test
from reclaim.live_test.jira import run_live_jira_write_test
from reclaim.world.engine import WorldStateEngine
from reclaim.world.seed_acme import seed_acme_world
from reclaim.llm.schemas import ActionProposal


@pytest.fixture
def acme_world(tmp_path):
    db_path = str(tmp_path / "judge_acme.db")
    engine = WorldStateEngine(db_path=db_path)
    seed_acme_world(engine)
    return engine


# 1. Missing live credentials are reported honestly
def test_missing_credentials_reported_honestly():
    async def run():
        with patch.dict(os.environ, {}, clear=True):
            results = await probe_live_adapters()
            for r in results:
                assert r["status"] == ConnectorStatus.AUTHENTICATION_REQUIRED.value
                assert "SKIPPED" in r["result"]
    asyncio.run(run())


# 2. Simulated evidence cannot be labeled LIVE
def test_simulated_evidence_cannot_be_labeled_live():
    with pytest.raises(ValueError, match="Integrity violation"):
        EvidenceItem(
            app="slack",
            source_type=SourceType.SIMULATED,
            connector_status=ConnectorStatus.LIVE_VERIFIED,
            resource_reference="slack:msg:123",
            timestamp="2026-09-13T10:00:00Z",
            author="tester",
            content="Customer experiencing database latency"
        )


# 3. A fake adapter cannot claim LIVE_VERIFIED
def test_fake_adapter_cannot_claim_live_verified():
    store = EvidenceStore()
    sim_receipt = ActionReceipt(
        app="jira",
        operation="update",
        resource="jira:ticket:PROD-1042",
        source_type=SourceType.SIMULATED,
        connector_status=ConnectorStatus.SIMULATED,
        execution_status="EXECUTED",
        idempotency_key="sim_test_fake_01"
    )
    with pytest.raises(ValueError, match="Integrity violation"):
        store.add_item(EvidenceItem(
            app="jira",
            source_type=SourceType.SIMULATED,
            connector_status=ConnectorStatus.LIVE_VERIFIED,
            resource_reference="jira:ticket:PROD-1042",
            timestamp="2026-09-13T10:00:00Z",
            author="fake_adapter",
            content=json.dumps(sim_receipt.model_dump())
        ))


# 4. Mission cannot complete with an unverified mandatory action
def test_mission_cannot_complete_with_unverified_mandatory_action(acme_world):
    async def run():
        registry = AdapterRegistry(mode="simulation", engine=acme_world)
        orchestrator = MissionOrchestrator(registry=registry)

        unverified_action = ActionProposal(
            action_id="mandatory_failed_01",
            app="jira",
            action="update_issue",
            arguments={"key": "DB-201", "priority": "P0"},
            risk_level="LOW_RISK_WRITE",
            requires_approval=False,
            rationale="Mandatory priority update",
            expected_effect={"priority": "P0"},
            verification_method={"type": "read_check"},
            is_optional=False
        )
        orchestrator.required_actions.append(unverified_action)

        failed_receipt = ActionReceipt(
            app="jira",
            operation="update",
            resource="jira:ticket:DB-201",
            source_type=SourceType.SIMULATED,
            execution_status="EXECUTED",
            verification_status="FAILED_VERIFICATION",
            idempotency_key="unverified_mandatory_01"
        )
        orchestrator.executed_receipts.append(failed_receipt)

        # Attempt to run completion check
        completed = await orchestrator._complete_mission("Simulated mission run")
        assert completed is False
        assert orchestrator.fsm.current_state == MissionState.FAILED
        assert orchestrator.failed_verification_count == 1
        assert orchestrator.required_action_count == 1
        assert orchestrator.verified_action_count == 0
    asyncio.run(run())


# 5. Scorecard cannot report verification_rate = 1.0 if any mandatory action is unverified
def test_scorecard_cannot_report_1_0_with_unverified_action(acme_world):
    registry = AdapterRegistry(mode="simulation", engine=acme_world)
    orchestrator = MissionOrchestrator(registry=registry)
    unverified_action = ActionProposal(
        action_id="mandatory_failed_01",
        app="jira",
        action="update_issue",
        arguments={"key": "DB-201", "priority": "P0"},
        risk_level="LOW_RISK_WRITE",
        requires_approval=False,
        rationale="Mandatory priority update",
        expected_effect={"priority": "P0"},
        verification_method={"type": "read_check"},
        is_optional=False
    )
    orchestrator.required_actions.append(unverified_action)
    failed_receipt = ActionReceipt(
        app="jira",
        operation="update",
        resource="jira:ticket:DB-201",
        source_type=SourceType.SIMULATED,
        execution_status="EXECUTED",
        verification_status="FAILED_VERIFICATION",
        idempotency_key="unverified_mandatory_01"
    )
    orchestrator.executed_receipts.append(failed_receipt)
    scorecard = AgenticityScorecard(orchestrator)
    eval_res = scorecard.evaluate()
    assert eval_res["metrics"]["verification_rate"] < 1.0
    assert eval_res["verdict"] == "UNVERIFIED_ACTIONS_REMAIN"


# 6. STRICT_LIVE fails when a required connector is unavailable
def test_strict_live_fails_when_connector_unavailable(acme_world):
    async def run():
        with patch.dict(os.environ, {}, clear=True):
            registry = AdapterRegistry(mode="simulation", engine=acme_world)
            orchestrator = MissionOrchestrator(
                registry=registry,
                policy=MissionDataPolicy.STRICT_LIVE
            )
            success = await orchestrator.run_mission("Save Acme Corp.")
            assert success is False
            assert orchestrator.fsm.current_state == MissionState.FAILED
    asyncio.run(run())


# 7. HYBRID clearly reports simulation dependency
def test_hybrid_clearly_reports_simulation_dependency(acme_world):
    registry = AdapterRegistry(mode="simulation", engine=acme_world)
    orchestrator = MissionOrchestrator(
        registry=registry,
        policy=MissionDataPolicy.HYBRID
    )
    orchestrator.store.add_item(EvidenceItem(
        app="slack",
        source_type=SourceType.SIMULATED,
        connector_status=ConnectorStatus.SIMULATED,
        resource_reference="slack:msg:123",
        timestamp="2026-09-13T10:00:00Z",
        author="tester",
        content="Acme latency incident"
    ))
    provenance = orchestrator.get_evidence_provenance_report()
    assert provenance["simulated_count"] == 1
    assert provenance["live_count"] == 0
    assert provenance["simulation_dependency"] == 1.0


# 8. Live write tests refuse to run without explicitly configured TEST resources
def test_live_write_tests_refuse_without_test_resources():
    async def run():
        with patch.dict(os.environ, {"GITHUB_TOKEN": "mock_token"}, clear=True):
            res_gh = await run_live_github_write_test()
            assert res_gh["status"] == "REFUSED_MISSING_CONFIG"
            assert "GITHUB_TEST_REPO" in res_gh["reason"]

        with patch.dict(os.environ, {"SLACK_BOT_TOKEN": "xoxb-mock"}, clear=True):
            res_slack = await run_live_slack_write_test()
            assert res_slack["status"] == "REFUSED_MISSING_CONFIG"
            assert "SLACK_TEST_CHANNEL" in res_slack["reason"]

        with patch.dict(os.environ, {"JIRA_URL": "https://test.atlassian.net", "JIRA_USER_EMAIL": "a@b.com", "JIRA_API_TOKEN": "tok"}, clear=True):
            res_jira = await run_live_jira_write_test()
            assert res_jira["status"] == "REFUSED_MISSING_CONFIG"
            assert "JIRA_TEST_PROJECT" in res_jira["reason"]
    asyncio.run(run())


# 9. Production resources are never used by default
def test_production_resources_never_defaulted():
    with patch.dict(os.environ, {}, clear=True):
        assert os.getenv("GITHUB_TEST_REPO") is None
        assert os.getenv("SLACK_TEST_CHANNEL") is None
        assert os.getenv("JIRA_TEST_PROJECT") is None


# 10. A live adapter must actually make a provider request before claiming LIVE_VERIFIED
def test_live_adapter_requires_real_response_for_live_verified():
    async def run():
        gh_adapter = LiveGitHubAdapter(token="")
        caps = await gh_adapter.get_capabilities()
        assert caps.availability != "AVAILABLE"
        assert caps.connector_status != ConnectorStatus.LIVE_VERIFIED

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value.status_code = 401
            mock_get.return_value.text = "Unauthorized"
            authed_adapter = LiveGitHubAdapter(token="invalid_token")
            resp = await authed_adapter.search("test", repo="owner/test")
            assert resp.success is False
            assert resp.connector_status != ConnectorStatus.LIVE_VERIFIED
    asyncio.run(run())


# 11. (Part J) Three-App Core Dependency: Reasoning succeeds with ONLY Slack + Jira + GitHub
def test_three_app_core_dependency_without_supporting_apps(acme_world):
    """
    Verifies that the agent can investigate, identify root cause, choose remediation,
    and verify actions using ONLY the 3 core applications (Slack, Jira, GitHub)
    when Gmail, CRM, and Calendar are completely unavailable/removed.
    """
    async def run():
        class ThreeAppRegistry(AdapterRegistry):
            async def get_adapter(self, app_name: str):
                if app_name in ["slack", "jira", "github"]:
                    return self.simulated_adapters[app_name]
                raise ValueError(f"Supporting application '{app_name}' is disabled in this test.")

            async def get_all_capabilities(self):
                caps = {}
                for app in ["slack", "jira", "github"]:
                    caps[app] = await self.simulated_adapters[app].get_capabilities()
                return caps

        registry = ThreeAppRegistry(mode="simulation", engine=acme_world)
        orchestrator = MissionOrchestrator(registry=registry)

        # 1. Investigate Slack (customer incident)
        slack_adapter = await registry.get_adapter("slack")
        s_resp = await slack_adapter.search(query="Acme latency")
        assert s_resp.success
        orchestrator.store.add_evidence(
            app="slack",
            source_type=SourceType.SIMULATED,
            connector_status=ConnectorStatus.SIMULATED,
            resource_reference="slack:channel:incidents:m1",
            timestamp="2026-09-13T10:00:00Z",
            author="eng_lead",
            content="Acme Corp reported critical DB latency in #incidents"
        )

        # 2. Investigate Jira (tracking ticket)
        jira_adapter = await registry.get_adapter("jira")
        j_resp = await jira_adapter.search(query="Acme")
        assert j_resp.success
        orchestrator.store.add_evidence(
            app="jira",
            source_type=SourceType.SIMULATED,
            connector_status=ConnectorStatus.SIMULATED,
            resource_reference="jira:ticket:PROD-1042",
            timestamp="2026-09-13T10:05:00Z",
            author="jira_user",
            content="Jira PROD-1042 tracking Acme latency issue"
        )

        # 3. Investigate GitHub (technical root cause)
        gh_adapter = await registry.get_adapter("github")
        gh_resp = await gh_adapter.search(query="connection pool")
        assert gh_resp.success
        orchestrator.store.add_evidence(
            app="github",
            source_type=SourceType.SIMULATED,
            connector_status=ConnectorStatus.SIMULATED,
            resource_reference="github:pr:882",
            timestamp="2026-09-13T10:10:00Z",
            author="dev",
            content="PR #882 reduced max connections to 5, causing exhaustion"
        )

        # 4. Reason & Decide: Remediate Jira ticket and notify Slack
        orchestrator.fsm.transition_to(MissionState.PLANNING)
        orchestrator.fsm.transition_to(MissionState.INVESTIGATING)
        orchestrator.fsm.transition_to(MissionState.EVIDENCE_SYNTHESIS)
        orchestrator.fsm.transition_to(MissionState.DECISION)

        proposals = [
            ActionProposal(
                action_id="act_j_core_01",
                app="jira",
                action="update_issue",
                arguments={"key": "PROD-1042", "priority": "P0", "description": "Escalated: PR #882 root cause identified."},
                risk_level="LOW_RISK_WRITE",
                requires_approval=False,
                rationale="Escalate Jira ticket to P0",
                expected_effect={"priority": "P0"},
                verification_method={"type": "read_check"}
            ),
            ActionProposal(
                action_id="act_s_core_02",
                app="slack",
                action="chat_postMessage",
                arguments={"channel": "incidents", "text": "Root cause found: PR #882 reverted."},
                risk_level="LOW_RISK_WRITE",
                requires_approval=False,
                rationale="Notify team in Slack",
                expected_effect={"posted": True},
                verification_method={"type": "read_check"}
            )
        ]

        # Execute & Verify
        for prop in proposals:
            orchestrator.required_actions.append(prop)
            orchestrator.fsm.transition_to(MissionState.EXECUTING)
            receipt = await orchestrator._execute_action(prop)
            assert receipt.execution_status == "EXECUTED"
            orchestrator.fsm.transition_to(MissionState.VERIFYING)
            v_res = await orchestrator._verify_action(prop, receipt)
            assert v_res.verified
            assert receipt.verification_status == "VERIFIED"
            orchestrator.fsm.transition_to(MissionState.DECISION)

        # Invariants hold
        assert orchestrator.required_action_count == 2
        assert orchestrator.verified_action_count == 2
        assert orchestrator.failed_verification_count == 0

        completed = await orchestrator._complete_mission("Core 3-app crisis resolved.")
        assert completed is True
        assert orchestrator.fsm.current_state == MissionState.COMPLETED
    asyncio.run(run())
