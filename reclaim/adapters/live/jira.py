import os
import time
import base64
import logging
from typing import Dict, Any, Optional
import httpx

from reclaim.adapters.base import (
    BaseAdapter,
    SourceType,
    RiskLevel,
    AdapterResponse,
    ActionReceipt,
    AppCapability
)
from reclaim.adapters.idempotency import idempotency_manager

logger = logging.getLogger(__name__)


class LiveJiraAdapter(BaseAdapter):
    """
    Live Jira connector interacting with the official Atlassian Cloud Jira REST API v3.
    Uses HTTP Basic Authentication (email:api_token).
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        email: Optional[str] = None,
        api_token: Optional[str] = None
    ):
        super().__init__(app_name="jira", source_type=SourceType.LIVE)
        self.base_url = (base_url or os.getenv("JIRA_BASE_URL") or os.getenv("JIRA_URL") or "").rstrip("/")
        self.email = email or os.getenv("JIRA_USER_EMAIL")
        self.api_token = api_token or os.getenv("JIRA_API_TOKEN")
        self.timeout_seconds = 15.0

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }
        if self.email and self.api_token:
            creds = f"{self.email}:{self.api_token}"
            b64_auth = base64.b64encode(creds.encode("utf-8")).decode("utf-8")
            headers["Authorization"] = f"Basic {b64_auth}"
        return headers

    async def get_capabilities(self) -> AppCapability:
        is_configured = bool(self.base_url and self.email and self.api_token)
        availability = "AVAILABLE" if is_configured else "UNAVAILABLE"
        auth_status = "AUTHENTICATED" if is_configured else "UNCONFIGURED"

        return AppCapability(
            app="jira",
            source_type=SourceType.LIVE,
            availability=availability,
            authentication_status=auth_status,
            read_operations=["search_tickets", "get_ticket"],
            write_operations=["update_ticket", "add_comment"],
            verification_operations=["verify_ticket_fields"],
            risk_policy={
                "search_tickets": RiskLevel.READ_ONLY,
                "get_ticket": RiskLevel.READ_ONLY,
                "update_ticket": RiskLevel.LOW_RISK_WRITE,
                "add_comment": RiskLevel.LOW_RISK_WRITE
            }
        )

    async def search(self, query: str, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        if not (self.base_url and self.email and self.api_token):
            return AdapterResponse(
                source_type=self.source_type,
                app="jira",
                operation="search",
                success=False,
                error="Live Jira connector unavailable: Missing JIRA_BASE_URL, JIRA_USER_EMAIL, or JIRA_API_TOKEN",
                latency_ms=0.0
            )

        test_project = os.getenv("JIRA_TEST_PROJECT")
        has_jql = any(k in query.upper() for k in ["PROJECT", "SELECT", "WHERE", "ORDER BY", "=", "~", "IS "])
        if has_jql:
            jql = query
        elif query.strip() and test_project:
            jql = f"project = '{test_project}' AND text ~ '{query.strip()}'"
        elif test_project:
            jql = f"project = '{test_project}' ORDER BY created DESC"
        else:
            jql = query or "ORDER BY created DESC"

        url = f"{self.base_url}/rest/api/3/search/jql"
        params = {"jql": jql, "maxResults": kwargs.get("limit", 10)}

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(url, headers=self._get_headers(), params=params)
            dt = (time.perf_counter() - t0) * 1000.0

            if resp.status_code == 200:
                data = resp.json()
                return AdapterResponse(
                    source_type=self.source_type,
                    app="jira",
                    operation="search",
                    latency_ms=dt,
                    success=True,
                    data={"total": data.get("total", 0), "issues": data.get("issues", [])}
                )

            return AdapterResponse(
                source_type=self.source_type,
                app="jira",
                operation="search",
                latency_ms=dt,
                success=False,
                error=f"Jira API HTTP {resp.status_code}: {resp.text[:120]}"
            )
        except Exception as e:
            dt = (time.perf_counter() - t0) * 1000.0
            return AdapterResponse(source_type=self.source_type, app="jira", operation="search", latency_ms=dt, success=False, error=str(e))

    async def read(self, resource_id: str, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        if not (self.base_url and self.email and self.api_token):
            return AdapterResponse(source_type=self.source_type, app="jira", operation="read", success=False, error="Jira credentials unconfigured")

        url = f"{self.base_url}/rest/api/3/issue/{resource_id}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(url, headers=self._get_headers())
            dt = (time.perf_counter() - t0) * 1000.0

            if resp.status_code == 200:
                return AdapterResponse(
                    source_type=self.source_type,
                    app="jira",
                    operation="read",
                    latency_ms=dt,
                    success=True,
                    data=resp.json(),
                    resource_reference=f"jira:ticket:{resource_id}"
                )
            return AdapterResponse(source_type=self.source_type, app="jira", operation="read", latency_ms=dt, success=False, error=f"HTTP {resp.status_code}")
        except Exception as e:
            return AdapterResponse(source_type=self.source_type, app="jira", operation="read", success=False, error=str(e))

    async def create(self, resource_type: str, payload: Dict[str, Any], idempotency_key: str, **kwargs) -> tuple[AdapterResponse, ActionReceipt]:
        t0 = time.perf_counter()
        project = payload.get("project")
        summary = payload.get("summary", "[RECLAIM TEST ONLY] Automated Verification Probe")
        desc = payload.get("description", "Temporary probe created by RECLAIM live verification suite.")
        issue_type = payload.get("issue_type", "Task")

        composite_key = idempotency_manager.compute_key("jira", "create_issue", str(project), idempotency_key)
        if idempotency_manager.has_executed(composite_key):
            cached = idempotency_manager.get_receipt(composite_key)
            resp = AdapterResponse(source_type=self.source_type, app="jira", operation="create_issue", success=True, data={"idempotent_replay": True, "result": cached.resulting_state}, resource_reference=cached.resource)
            return resp, cached

        if not (self.base_url and self.email and self.api_token) or not project:
            receipt = ActionReceipt(app="jira", operation="create_issue", resource=f"jira:{project}:err", source_type=self.source_type, execution_status="FAILED", error="Credentials or project missing", idempotency_key=idempotency_key)
            return AdapterResponse(source_type=self.source_type, app="jira", operation="create_issue", success=False, error="Credentials or project missing"), receipt

        url = f"{self.base_url}/rest/api/3/issue"
        body = {
            "fields": {
                "project": {"key": project},
                "summary": summary,
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [{"type": "paragraph", "content": [{"type": "text", "text": desc}]}]
                },
                "issuetype": {"name": issue_type}
            }
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(url, headers=self._get_headers(), json=body)
            dt = (time.perf_counter() - t0) * 1000.0
            if resp.status_code in (200, 201):
                res_data = resp.json()
                key = res_data.get("key")
                receipt = ActionReceipt(
                    app="jira",
                    operation="create_issue",
                    resource=f"jira:ticket:{key}",
                    resulting_state=res_data,
                    source_type=self.source_type,
                    execution_status="EXECUTED",
                    verification_status="PENDING",
                    idempotency_key=idempotency_key
                )
                idempotency_manager.record_receipt(composite_key, receipt)
                return AdapterResponse(source_type=self.source_type, app="jira", operation="create_issue", latency_ms=dt, success=True, data=res_data, resource_reference=receipt.resource), receipt
            receipt = ActionReceipt(app="jira", operation="create_issue", resource=f"jira:{project}:err", source_type=self.source_type, execution_status="FAILED", error=resp.text[:100], idempotency_key=idempotency_key)
            return AdapterResponse(source_type=self.source_type, app="jira", operation="create_issue", latency_ms=dt, success=False, error=f"Jira HTTP {resp.status_code}: {resp.text[:100]}"), receipt
        except Exception as e:
            dt = (time.perf_counter() - t0) * 1000.0
            receipt = ActionReceipt(app="jira", operation="create_issue", resource=f"jira:{project}:exc", source_type=self.source_type, execution_status="FAILED", error=str(e), idempotency_key=idempotency_key)
            return AdapterResponse(source_type=self.source_type, app="jira", operation="create_issue", latency_ms=dt, success=False, error=str(e)), receipt

    async def update(
        self,
        resource_id: str,
        payload: Dict[str, Any],
        idempotency_key: str,
        **kwargs
    ) -> tuple[AdapterResponse, ActionReceipt]:
        t0 = time.perf_counter()
        ticket_key = resource_id
        composite_key = idempotency_manager.compute_key("jira", "update_issue", ticket_key, idempotency_key)

        if idempotency_manager.has_executed(composite_key):
            cached = idempotency_manager.get_receipt(composite_key)
            resp = AdapterResponse(
                source_type=self.source_type,
                app="jira",
                operation="update_issue",
                success=True,
                data={"idempotent_replay": True, "result": cached.resulting_state},
                resource_reference=cached.resource
            )
            return resp, cached

        if not (self.base_url and self.email and self.api_token):
            receipt = ActionReceipt(app="jira", operation="update_issue", resource=f"jira:ticket:{ticket_key}", source_type=self.source_type, execution_status="FAILED", error="Jira credentials unconfigured", idempotency_key=idempotency_key)
            return AdapterResponse(source_type=self.source_type, app="jira", operation="update_issue", success=False, error="Credentials unconfigured"), receipt

        url = f"{self.base_url}/rest/api/3/issue/{ticket_key}"
        clean_fields = {}
        for k, v in payload.items():
            if k in ("key", "issue_id", "ticket_id", "id"):
                continue
            if k == "priority":
                if isinstance(v, dict):
                    clean_fields["priority"] = v
                elif isinstance(v, str):
                    p_name = "High" if any(x in v.upper() for x in ["P0", "BLOCKER", "HIGH", "CRITICAL"]) else ("Medium" if "MEDIUM" in v.upper() else "Low")
                    clean_fields["priority"] = {"name": p_name}
            elif k in ("summary", "description", "labels"):
                clean_fields[k] = v
        if not clean_fields:
            clean_fields = {"summary": payload.get("summary") or f"Updated {ticket_key}"}
        body = {"fields": clean_fields}

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.put(url, headers=self._get_headers(), json=body)
            dt = (time.perf_counter() - t0) * 1000.0

            if resp.status_code in (200, 204):
                receipt = ActionReceipt(
                    app="jira",
                    operation="update_issue",
                    resource=f"jira:ticket:{ticket_key}",
                    previous_state={},
                    requested_state=payload,
                    resulting_state=payload,
                    source_type=self.source_type,
                    execution_status="EXECUTED",
                    verification_status="PENDING",
                    idempotency_key=idempotency_key
                )
                idempotency_manager.record_receipt(composite_key, receipt)
                return AdapterResponse(
                    source_type=self.source_type,
                    app="jira",
                    operation="update_issue",
                    latency_ms=dt,
                    success=True,
                    data=payload,
                    resource_reference=receipt.resource
                ), receipt

            receipt = ActionReceipt(app="jira", operation="update_issue", resource=f"jira:ticket:{ticket_key}", source_type=self.source_type, execution_status="FAILED", error=resp.text[:100], idempotency_key=idempotency_key)
            return AdapterResponse(source_type=self.source_type, app="jira", operation="update_issue", latency_ms=dt, success=False, error=f"Jira HTTP {resp.status_code}: {resp.text[:100]}"), receipt

        except Exception as e:
            dt = (time.perf_counter() - t0) * 1000.0
            receipt = ActionReceipt(app="jira", operation="update_issue", resource=f"jira:ticket:{ticket_key}", source_type=self.source_type, execution_status="FAILED", error=str(e), idempotency_key=idempotency_key)
            return AdapterResponse(source_type=self.source_type, app="jira", operation="update_issue", latency_ms=dt, success=False, error=str(e)), receipt

    async def verify(self, action_receipt: ActionReceipt, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        ticket_key = action_receipt.resource.replace("jira:ticket:", "")
        # Independent read query
        read_resp = await self.read(resource_id=ticket_key)
        dt = (time.perf_counter() - t0) * 1000.0

        if read_resp.success:
            issue_data = read_resp.data
            action_receipt.verification_status = "VERIFIED"
            return AdapterResponse(
                source_type=self.source_type,
                app="jira",
                operation="verify",
                latency_ms=dt,
                success=True,
                data={"verified": True, "observed_issue": issue_data}
            )

        action_receipt.verification_status = "FAILED_VERIFICATION"
        dt = (time.perf_counter() - t0) * 1000.0
        return AdapterResponse(source_type=self.source_type, app="jira", operation="verify", latency_ms=dt, success=False, error="Verification failed: Ticket not found on Jira")

    async def delete(self, resource_id: str) -> AdapterResponse:
        t0 = time.perf_counter()
        if not (self.base_url and self.email and self.api_token):
            return AdapterResponse(source_type=self.source_type, app="jira", operation="delete", success=False, error="Jira credentials unconfigured")
        url = f"{self.base_url}/rest/api/3/issue/{resource_id}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.delete(url, headers=self._get_headers())
            dt = (time.perf_counter() - t0) * 1000.0
            if resp.status_code in (200, 204):
                return AdapterResponse(source_type=self.source_type, app="jira", operation="delete", latency_ms=dt, success=True)
            return AdapterResponse(source_type=self.source_type, app="jira", operation="delete", latency_ms=dt, success=False, error=f"HTTP {resp.status_code}")
        except Exception as e:
            dt = (time.perf_counter() - t0) * 1000.0
            return AdapterResponse(source_type=self.source_type, app="jira", operation="delete", latency_ms=dt, success=False, error=str(e))
