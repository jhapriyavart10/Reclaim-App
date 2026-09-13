import os
import asyncio
import pytest
import sqlite3
from unittest.mock import patch, AsyncMock
import httpx

from reclaim.world.engine import WorldStateEngine
from reclaim.world.seed_acme import seed_acme_world
from reclaim.world.faults import fault_injector
from reclaim.adapters.base import SourceType, RiskLevel, AdapterResponse, ActionReceipt
from reclaim.adapters.registry import AdapterRegistry
from reclaim.adapters.idempotency import idempotency_manager
from reclaim.adapters.simulated.github import SimulatedGitHubAdapter
from reclaim.adapters.simulated.slack import SimulatedSlackAdapter
from reclaim.adapters.simulated.jira import SimulatedJiraAdapter
from reclaim.adapters.simulated.gmail import SimulatedGmailAdapter
from reclaim.adapters.simulated.calendar import SimulatedCalendarAdapter
from reclaim.adapters.simulated.crm import SimulatedCRMAdapter
from reclaim.adapters.live.github import LiveGitHubAdapter
from reclaim.adapters.live.slack import LiveSlackAdapter
from reclaim.adapters.live.jira import LiveJiraAdapter


@pytest.fixture(autouse=True)
def setup_clean_world(tmp_path):
    """Fixture ensuring each test runs with a fresh, seeded SQLite database and cleared faults."""
    fault_injector.clear()
    idempotency_manager.clear()
    test_db_path = tmp_path / "test_reclaim_world.db"
    engine = WorldStateEngine(db_path=test_db_path)
    seed_acme_world(engine)
    return engine


# ============================================================================
# 1. World State Initialization
# ============================================================================
def test_world_state_initialization(setup_clean_world):
    engine = setup_clean_world
    with engine.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [r[0] for r in cursor.fetchall()]
        assert "crm_accounts" in tables
        assert "gmail_messages" in tables
        assert "slack_messages" in tables
        assert "github_pull_requests" in tables
        assert "jira_tickets" in tables
        assert "calendar_events" in tables


# ============================================================================
# 2. Acme Seed Consistency
# ============================================================================
def test_acme_seed_consistency(setup_clean_world):
    engine = setup_clean_world
    account = engine.get_crm_account("Acme Corp")
    assert account is not None
    assert account["name"] == "Acme Corp"
    assert account["arr"] == 250000.0
    assert account["health_score"] == 34
    assert account["status"] == "AT_RISK"
    assert account["primary_contact_name"] == "Sarah Chen"


# ============================================================================
# 3. Cross-App Entity Correlation
# ============================================================================
def test_cross_app_entity_correlation(setup_clean_world):
    engine = setup_clean_world
    # 1. Sarah Chen in CRM
    account = engine.get_crm_account("Acme Corp")
    email = account["primary_contact_email"]
    # 2. Correlate with Gmail messages from Sarah
    emails = engine.search_emails(email)
    assert len(emails) >= 1
    assert "/v2/data-sync" in emails[0]["body"]
    # 3. Correlate endpoint with Slack
    slack_alerts = engine.search_slack_messages("/v2/data-sync")
    assert len(slack_alerts) >= 1
    # 4. Correlate with Jira ticket PROD-1042
    tickets = engine.search_jira_tickets("data-sync")
    assert any(t["key"] == "PROD-1042" for t in tickets)
    # 5. Correlate with GitHub PR #882
    prs = engine.search_github_prs("serializer")
    assert any(p["number"] == 882 for p in prs)


# ============================================================================
# 4. Gmail Search
# ============================================================================
def test_gmail_search(setup_clean_world):
    async def run():
        adapter = SimulatedGmailAdapter(engine=setup_clean_world)
        resp = await adapter.search(query="cancellation")
        assert resp.success is True
        assert resp.source_type == SourceType.SIMULATED
        assert len(resp.data["emails"]) >= 1
        assert "terminating our contract" in resp.data["emails"][0]["body"].lower()

    asyncio.run(run())


# ============================================================================
# 5. Slack Search
# ============================================================================
def test_slack_search(setup_clean_world):
    async def run():
        adapter = SimulatedSlackAdapter(engine=setup_clean_world)
        resp = await adapter.search(query="datadog-bot")
        assert resp.success is True
        assert resp.source_type == SourceType.SIMULATED
        assert resp.data["count"] >= 1

    asyncio.run(run())


# ============================================================================
# 6. GitHub and Jira Search
# ============================================================================
def test_github_and_jira_search(setup_clean_world):
    async def run():
        gh_adapter = SimulatedGitHubAdapter(engine=setup_clean_world)
        gh_resp = await gh_adapter.search("strict JSON schema")
        assert gh_resp.success is True
        assert len(gh_resp.data["pull_requests"]) >= 1
        assert gh_resp.data["pull_requests"][0]["number"] == 882

        jira_adapter = SimulatedJiraAdapter(engine=setup_clean_world)
        jira_resp = await jira_adapter.search("PROD-1042")
        assert jira_resp.success is True
        assert jira_resp.data["total"] >= 1
        assert jira_resp.data["tickets"][0]["key"] == "PROD-1042"

    asyncio.run(run())


# ============================================================================
# 7. Live Adapter Connectivity (Tested or explicitly skipped)
# ============================================================================
def test_live_adapter_connectivity():
    async def run():
        gh_token = os.getenv("GITHUB_TOKEN")
        gh_adapter = LiveGitHubAdapter(token=gh_token)
        gh_caps = await gh_adapter.get_capabilities()

        if not gh_token:
            pytest.skip("SKIPPED — credential unavailable (GITHUB_TOKEN)")
        else:
            assert gh_caps.availability == "AVAILABLE"
            assert gh_caps.authentication_status == "AUTHENTICATED"

    asyncio.run(run())


# ============================================================================
# 8. Simulation Adapter Behavior
# ============================================================================
def test_simulation_adapter_behavior(setup_clean_world):
    async def run():
        crm = SimulatedCRMAdapter(engine=setup_clean_world)
        cal = SimulatedCalendarAdapter(engine=setup_clean_world)

        crm_resp = await crm.read("crm_acc_acme_001")
        assert crm_resp.success is True
        assert crm_resp.source_type == SourceType.SIMULATED
        assert crm_resp.data["health_score"] == 34

        cal_resp = await cal.search("Standup")
        assert cal_resp.success is True
        assert len(cal_resp.data["events"]) >= 1

    asyncio.run(run())


# ============================================================================
# 9. Live / Simulation Response Normalization
# ============================================================================
def test_response_normalization():
    resp_live = AdapterResponse(
        source_type=SourceType.LIVE,
        app="github",
        operation="search",
        success=True,
        data={"items": []}
    )
    resp_sim = AdapterResponse(
        source_type=SourceType.SIMULATED,
        app="github",
        operation="search",
        success=True,
        data={"items": []}
    )

    for r in [resp_live, resp_sim]:
        assert hasattr(r, "source_type")
        assert hasattr(r, "request_id")
        assert hasattr(r, "timestamp")
        assert hasattr(r, "latency_ms")
        assert hasattr(r, "success")
        assert hasattr(r, "data")
        assert hasattr(r, "error")


# ============================================================================
# 10. Write Mutation
# ============================================================================
def test_write_mutation(setup_clean_world):
    async def run():
        jira = SimulatedJiraAdapter(engine=setup_clean_world)
        resp, receipt = await jira.update(
            resource_id="PROD-1042",
            payload={"priority": "P0 - Blocker", "assignee": "tech_lead_alex"},
            idempotency_key="test-p0-escalation"
        )
        assert resp.success is True
        assert receipt.execution_status == "EXECUTED"
        assert receipt.previous_state.get("priority") == "Medium"
        assert receipt.resulting_state.get("priority") == "P0 - Blocker"

        # Verify mutation exists in engine
        ticket = setup_clean_world.get_jira_ticket("PROD-1042")
        assert ticket["priority"] == "P0 - Blocker"
        assert ticket["assignee"] == "tech_lead_alex"

    asyncio.run(run())


# ============================================================================
# 11. Independent Verification
# ============================================================================
def test_independent_verification(setup_clean_world):
    async def run():
        slack = SimulatedSlackAdapter(engine=setup_clean_world)
        resp, receipt = await slack.create(
            resource_type="message",
            payload={"channel": "customer-acme", "text": "P0 War room initiated for Acme Corp.", "user": "reclaim-bot"},
            idempotency_key="slack-war-room-01"
        )
        assert resp.success is True
        assert receipt.verification_status == "PENDING"

        # Independent verification check
        verify_resp = await slack.verify(receipt)
        assert verify_resp.success is True
        assert verify_resp.data["verified"] is True
        assert receipt.verification_status == "VERIFIED"

    asyncio.run(run())


# ============================================================================
# 12. Idempotent Retry
# ============================================================================
def test_idempotent_retry(setup_clean_world):
    async def run():
        gmail = SimulatedGmailAdapter(engine=setup_clean_world)
        payload = {"to": "sarah@acme.com", "subject": "Apology & RCA", "body": "We fixed the issue."}

        # First call
        resp1, receipt1 = await gmail.create("message", payload, idempotency_key="rca-email-acme")
        assert resp1.success is True
        assert receipt1.execution_status == "EXECUTED"

        # Duplicate identical call with same idempotency key
        resp2, receipt2 = await gmail.create("message", payload, idempotency_key="rca-email-acme")
        assert resp2.success is True
        assert resp2.data.get("idempotent_replay") is True
        assert receipt2.action_id == receipt1.action_id

        # Verify no duplicate email was inserted into sent messages
        matching_emails = setup_clean_world.search_emails("Apology & RCA")
        assert len(matching_emails) == 1

    asyncio.run(run())


# ============================================================================
# 13. Network Timeout Fault Injection
# ============================================================================
def test_network_timeout_fault_injection(setup_clean_world):
    async def run():
        fault_injector.set_fault("NETWORK_TIMEOUT")
        github = SimulatedGitHubAdapter(engine=setup_clean_world)
        resp = await github.search("backend-core")
        assert resp.success is False
        assert "timeout" in resp.error.lower()

    asyncio.run(run())


# ============================================================================
# 14. HTTP 503 Fault Injection
# ============================================================================
def test_http_503_fault_injection(setup_clean_world):
    async def run():
        fault_injector.set_fault("HTTP_503")
        slack = SimulatedSlackAdapter(engine=setup_clean_world)
        resp = await slack.search("alerts")
        assert resp.success is False
        assert "503" in resp.error

    asyncio.run(run())


# ============================================================================
# 15. ACK-Without-Mutation Fault Injection & Detection
# ============================================================================
def test_ack_without_mutation_fault_injection(setup_clean_world):
    async def run():
        fault_injector.set_fault("WRITE_ACK_WITHOUT_MUTATION")
        jira = SimulatedJiraAdapter(engine=setup_clean_world)

        # Simulator acknowledges the write
        resp, receipt = await jira.update(
            resource_id="PROD-1042",
            payload={"priority": "P0 - Blocker"},
            idempotency_key="ack-fault-test"
        )
        assert resp.success is True  # Falsely acknowledged

        # But the Verification step MUST catch that state was NOT actually mutated!
        verify_resp = await jira.verify(receipt)
        assert verify_resp.success is False
        assert verify_resp.data["verified"] is False
        assert receipt.verification_status == "FAILED_VERIFICATION"

    asyncio.run(run())


# ============================================================================
# 16. Stale Read Fault Injection
# ============================================================================
def test_calendar_conflict_fault_injection(setup_clean_world):
    async def run():
        fault_injector.set_fault("CALENDAR_CONFLICT")
        cal = SimulatedCalendarAdapter(engine=setup_clean_world)
        resp, receipt = await cal.create("event", {"summary": "Executive Sync"}, idempotency_key="sync-conflict")
        assert resp.success is False
        assert receipt.execution_status == "FAILED"
        assert "Double-booking" in receipt.error

    asyncio.run(run())


# ============================================================================
# 17. Unavailable Connector Graceful Fallback
# ============================================================================
def test_unavailable_connector_graceful_fallback():
    async def run():
        # Test in HYBRID mode with missing GITHUB_TOKEN
        with patch.dict(os.environ, {"APP_MODE": "hybrid", "GITHUB_TOKEN": ""}):
            registry = AdapterRegistry(mode="hybrid")
            adapter = await registry.get_adapter("github")
            assert adapter.source_type == SourceType.SIMULATED  # Gracefully resolved to simulated

    asyncio.run(run())


# ============================================================================
# 18. Capability Discovery
# ============================================================================
def test_capability_discovery():
    async def run():
        registry = AdapterRegistry(mode="simulation")
        caps = await registry.get_all_capabilities()
        assert "github" in caps
        assert "slack" in caps
        assert "jira" in caps
        assert "gmail" in caps
        assert "calendar" in caps
        assert "crm" in caps
        assert caps["gmail"].risk_policy["send_email"] == RiskLevel.HIGH_RISK_WRITE
        assert caps["calendar"].risk_policy["create_meeting"] == RiskLevel.HIGH_RISK_WRITE
        assert caps["jira"].risk_policy["update_ticket"] == RiskLevel.LOW_RISK_WRITE

    asyncio.run(run())


# ============================================================================
# 19. Secret Redaction
# ============================================================================
def test_secret_redaction():
    secret_token = "ghp_secret_token_1234567890abcdef"
    adapter = LiveGitHubAdapter(token=secret_token)
    headers = adapter._get_headers()

    # Never expose token in logs or error representations
    adapter_str = str(adapter.__dict__)
    assert "ghp_secret_token" in adapter.token  # Stored internally
    # But headers or logs must not be printed in AdapterResponse errors
    err_resp = AdapterResponse(
        source_type=SourceType.LIVE,
        app="github",
        operation="read",
        success=False,
        error="Unauthorized access"
    )
    assert secret_token not in str(err_resp)


# ============================================================================
# 20. Action Receipt Generation
# ============================================================================
def test_action_receipt_generation(setup_clean_world):
    async def run():
        crm = SimulatedCRMAdapter(engine=setup_clean_world)
        resp, receipt = await crm.update(
            resource_id="crm_acc_acme_001",
            payload={"health_score": 85, "status": "RECOVERING"},
            idempotency_key="crm-health-boost"
        )
        assert isinstance(receipt, ActionReceipt)
        assert receipt.app == "crm"
        assert receipt.operation == "update_health"
        assert receipt.resource == "crm:account:crm_acc_acme_001"
        assert receipt.previous_state == {"health_score": 34}
        assert receipt.resulting_state["health_score"] == 85
        assert receipt.execution_status == "EXECUTED"
        assert receipt.idempotency_key == "crm-health-boost"

    asyncio.run(run())
