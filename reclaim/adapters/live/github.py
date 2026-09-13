import os
import time
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

GITHUB_API_BASE = "https://api.github.com"


class LiveGitHubAdapter(BaseAdapter):
    """
    Live GitHub connector interacting with the official GitHub REST API v3.
    Uses Personal Access Token (GITHUB_TOKEN) via HTTP Bearer Authentication.
    """

    def __init__(self, token: Optional[str] = None):
        super().__init__(app_name="github", source_type=SourceType.LIVE)
        self.token = token if token is not None else os.getenv("GITHUB_TOKEN")
        self.timeout_seconds = 15.0

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "RECLAIM-Business-Rescue-Agent"
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def get_capabilities(self) -> AppCapability:
        is_configured = bool(self.token)
        availability = "AVAILABLE" if is_configured else "UNAVAILABLE"
        auth_status = "AUTHENTICATED" if is_configured else "UNCONFIGURED"

        return AppCapability(
            app="github",
            source_type=SourceType.LIVE,
            availability=availability,
            authentication_status=auth_status,
            read_operations=["search_issues", "search_commits", "get_pull_request", "get_issue"],
            write_operations=["create_issue", "create_comment"],
            verification_operations=["verify_issue_exists", "verify_comment_exists"],
            risk_policy={
                "search_issues": RiskLevel.READ_ONLY,
                "search_commits": RiskLevel.READ_ONLY,
                "get_pull_request": RiskLevel.READ_ONLY,
                "create_issue": RiskLevel.LOW_RISK_WRITE,
                "create_comment": RiskLevel.LOW_RISK_WRITE
            }
        )

    async def search(self, query: str, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        if not self.token:
            return AdapterResponse(
                source_type=self.source_type,
                app="github",
                operation="search",
                success=False,
                error="Live GitHub connector unavailable: GITHUB_TOKEN is unconfigured",
                latency_ms=0.0
            )

        repo = kwargs.get("repo") or os.getenv("GITHUB_TEST_REPO") or os.getenv("GITHUB_REPO")
        q = f"repo:{repo} {query}".strip() if repo else query
        url = f"{GITHUB_API_BASE}/search/issues"
        params = {"q": q, "per_page": kwargs.get("limit", 10)}

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(url, headers=self._get_headers(), params=params)
            dt = (time.perf_counter() - t0) * 1000.0

            if resp.status_code == 200:
                data = resp.json()
                return AdapterResponse(
                    source_type=self.source_type,
                    app="github",
                    operation="search",
                    latency_ms=dt,
                    success=True,
                    data={"total_count": data.get("total_count", 0), "items": data.get("items", [])}
                )
            return AdapterResponse(
                source_type=self.source_type,
                app="github",
                operation="search",
                latency_ms=dt,
                success=False,
                error=f"GitHub API HTTP {resp.status_code}: {resp.text[:120]}"
            )
        except Exception as e:
            dt = (time.perf_counter() - t0) * 1000.0
            return AdapterResponse(source_type=self.source_type, app="github", operation="search", latency_ms=dt, success=False, error=str(e))

    async def read(self, resource_id: str, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        if not self.token:
            return AdapterResponse(source_type=self.source_type, app="github", operation="read", success=False, error="GITHUB_TOKEN unconfigured")

        repo = kwargs.get("repo")
        if not repo:
            return AdapterResponse(source_type=self.source_type, app="github", operation="read", success=False, error="Missing repo parameter")

        url = f"{GITHUB_API_BASE}/repos/{repo}/issues/{resource_id}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(url, headers=self._get_headers())
            dt = (time.perf_counter() - t0) * 1000.0
            if resp.status_code == 200:
                return AdapterResponse(
                    source_type=self.source_type,
                    app="github",
                    operation="read",
                    latency_ms=dt,
                    success=True,
                    data=resp.json(),
                    resource_reference=f"github:{repo}:issue:{resource_id}"
                )
            return AdapterResponse(source_type=self.source_type, app="github", operation="read", latency_ms=dt, success=False, error=f"HTTP {resp.status_code}")
        except Exception as e:
            return AdapterResponse(source_type=self.source_type, app="github", operation="read", success=False, error=str(e))

    async def create(
        self,
        resource_type: str,
        payload: Dict[str, Any],
        idempotency_key: str,
        **kwargs
    ) -> tuple[AdapterResponse, ActionReceipt]:
        t0 = time.perf_counter()
        repo = payload.get("repo")
        composite_key = idempotency_manager.compute_key("github", f"create_{resource_type}", str(repo), idempotency_key)

        # Idempotency Check
        if idempotency_manager.has_executed(composite_key):
            cached = idempotency_manager.get_receipt(composite_key)
            resp = AdapterResponse(
                source_type=self.source_type,
                app="github",
                operation=f"create_{resource_type}",
                success=True,
                data={"idempotent_replay": True, "result": cached.resulting_state},
                resource_reference=cached.resource
            )
            return resp, cached

        if not self.token or not repo:
            receipt = ActionReceipt(
                app="github",
                operation=f"create_{resource_type}",
                resource=f"github:{repo}:failed",
                source_type=self.source_type,
                execution_status="FAILED",
                error="Live credentials or repo missing",
                idempotency_key=idempotency_key
            )
            return AdapterResponse(source_type=self.source_type, app="github", operation=f"create_{resource_type}", success=False, error="Credentials or repo missing"), receipt

        url = f"{GITHUB_API_BASE}/repos/{repo}/issues"
        body = {
            "title": payload.get("title", "RECLAIM Incident Report"),
            "body": payload.get("body", "Generated by RECLAIM"),
            "labels": payload.get("labels", ["reclaim", "p0"])
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(url, headers=self._get_headers(), json=body)
            dt = (time.perf_counter() - t0) * 1000.0

            if resp.status_code in (200, 201):
                res_data = resp.json()
                issue_number = res_data.get("number")
                receipt = ActionReceipt(
                    app="github",
                    operation="create_issue",
                    resource=f"github:{repo}:issue:{issue_number}",
                    previous_state={},
                    requested_state=payload,
                    resulting_state={"issue_number": issue_number, "url": res_data.get("html_url"), "state": "open"},
                    source_type=self.source_type,
                    execution_status="EXECUTED",
                    verification_status="PENDING",
                    idempotency_key=idempotency_key
                )
                idempotency_manager.record_receipt(composite_key, receipt)
                return AdapterResponse(
                    source_type=self.source_type,
                    app="github",
                    operation="create_issue",
                    latency_ms=dt,
                    success=True,
                    data=receipt.resulting_state,
                    resource_reference=receipt.resource
                ), receipt

            receipt = ActionReceipt(app="github", operation="create_issue", resource=f"github:{repo}:err", source_type=self.source_type, execution_status="FAILED", error=resp.text[:100], idempotency_key=idempotency_key)
            return AdapterResponse(source_type=self.source_type, app="github", operation="create_issue", latency_ms=dt, success=False, error=f"GitHub HTTP {resp.status_code}: {resp.text[:100]}"), receipt

        except Exception as e:
            dt = (time.perf_counter() - t0) * 1000.0
            receipt = ActionReceipt(app="github", operation="create_issue", resource=f"github:{repo}:exc", source_type=self.source_type, execution_status="FAILED", error=str(e), idempotency_key=idempotency_key)
            return AdapterResponse(source_type=self.source_type, app="github", operation="create_issue", latency_ms=dt, success=False, error=str(e)), receipt

    async def update(self, resource_id: str, payload: Dict[str, Any], idempotency_key: str, **kwargs) -> tuple[AdapterResponse, ActionReceipt]:
        t0 = time.perf_counter()
        repo = kwargs.get("repo") or payload.get("repo")
        if not self.token or not repo:
            receipt = ActionReceipt(app="github", operation="update_issue", resource=f"github:{repo}:issue:{resource_id}", source_type=self.source_type, execution_status="FAILED", error="Token or repo missing", idempotency_key=idempotency_key)
            return AdapterResponse(source_type=self.source_type, app="github", operation="update_issue", success=False, error="Credentials or repo missing"), receipt

        url = f"{GITHUB_API_BASE}/repos/{repo}/issues/{resource_id}"
        update_fields = {k: v for k, v in payload.items() if k in ("state", "title", "body", "labels")}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.patch(url, headers=self._get_headers(), json=update_fields)
            dt = (time.perf_counter() - t0) * 1000.0
            if resp.status_code in (200, 204):
                res_json = resp.json()
                receipt = ActionReceipt(
                    app="github",
                    operation="update_issue",
                    resource=f"github:{repo}:issue:{resource_id}",
                    resulting_state=res_json,
                    source_type=self.source_type,
                    execution_status="EXECUTED",
                    verification_status="PENDING",
                    idempotency_key=idempotency_key
                )
                return AdapterResponse(source_type=self.source_type, app="github", operation="update_issue", latency_ms=dt, success=True, data=res_json), receipt
            receipt = ActionReceipt(app="github", operation="update_issue", resource=f"github:{repo}:issue:{resource_id}", source_type=self.source_type, execution_status="FAILED", error=resp.text[:100], idempotency_key=idempotency_key)
            return AdapterResponse(source_type=self.source_type, app="github", operation="update_issue", latency_ms=dt, success=False, error=f"HTTP {resp.status_code}: {resp.text[:100]}"), receipt
        except Exception as e:
            dt = (time.perf_counter() - t0) * 1000.0
            receipt = ActionReceipt(app="github", operation="update_issue", resource=f"github:{repo}:issue:{resource_id}", source_type=self.source_type, execution_status="FAILED", error=str(e), idempotency_key=idempotency_key)
            return AdapterResponse(source_type=self.source_type, app="github", operation="update_issue", latency_ms=dt, success=False, error=str(e)), receipt

    async def verify(self, action_receipt: ActionReceipt, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        # Parse resource: github:{repo}:issue:{issue_number}
        parts = action_receipt.resource.split(":")
        if len(parts) >= 4 and parts[2] == "issue":
            repo = parts[1]
            issue_number = parts[3]
            # Independent read query to live GitHub API
            read_resp = await self.read(resource_id=issue_number, repo=repo)
            dt = (time.perf_counter() - t0) * 1000.0
            if read_resp.success:
                observed_issue = read_resp.data
                action_receipt.verification_status = "VERIFIED"
                return AdapterResponse(
                    source_type=self.source_type,
                    app="github",
                    operation="verify",
                    latency_ms=dt,
                    success=True,
                    data={"verified": True, "observed_issue": observed_issue}
                )

        action_receipt.verification_status = "FAILED_VERIFICATION"
        dt = (time.perf_counter() - t0) * 1000.0
        return AdapterResponse(source_type=self.source_type, app="github", operation="verify", latency_ms=dt, success=False, error="Verification failed: Issue not found on GitHub")
