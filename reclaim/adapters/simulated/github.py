import time
from typing import Dict, Any, List, Optional
from reclaim.adapters.base import (
    BaseAdapter,
    SourceType,
    RiskLevel,
    AdapterResponse,
    ActionReceipt,
    AppCapability
)
from reclaim.adapters.idempotency import idempotency_manager
from reclaim.world.engine import WorldStateEngine, world_engine
from reclaim.world.faults import fault_injector


class SimulatedGitHubAdapter(BaseAdapter):
    """
    Simulated GitHub adapter operating against the local SQLite World State Engine.
    Exposes full commit search, PR search, issue creation, and verification.
    """

    def __init__(self, engine: Optional[WorldStateEngine] = None):
        super().__init__(app_name="github", source_type=SourceType.SIMULATED)
        self.engine = engine or world_engine

    async def get_capabilities(self) -> AppCapability:
        return AppCapability(
            app="github",
            source_type=SourceType.SIMULATED,
            availability="AVAILABLE",
            authentication_status="AUTHENTICATED",
            read_operations=["search_prs", "search_commits", "get_pr", "get_issue"],
            write_operations=["create_issue", "add_comment"],
            verification_operations=["verify_issue_exists", "verify_comment_exists"],
            risk_policy={
                "search_prs": RiskLevel.READ_ONLY,
                "search_commits": RiskLevel.READ_ONLY,
                "get_pr": RiskLevel.READ_ONLY,
                "create_issue": RiskLevel.LOW_RISK_WRITE,
                "add_comment": RiskLevel.LOW_RISK_WRITE
            }
        )

    async def search(self, query: str, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        fault = fault_injector.active_fault
        if fault == "NETWORK_TIMEOUT":
            return AdapterResponse(source_type=self.source_type, app="github", operation="search", success=False, error="Connection timeout to GitHub API", latency_ms=100.0)
        if fault == "HTTP_503":
            return AdapterResponse(source_type=self.source_type, app="github", operation="search", success=False, error="GitHub Service Unavailable (503)", latency_ms=50.0)

        prs = self.engine.search_github_prs(query)
        commits = self.engine.search_github_commits(query)
        dt = (time.perf_counter() - t0) * 1000.0
        return AdapterResponse(
            source_type=self.source_type,
            app="github",
            operation="search",
            latency_ms=dt,
            success=True,
            data={"pull_requests": prs, "commits": commits}
        )

    async def read(self, resource_id: str, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        if resource_id.isdigit():
            pr = self.engine.get_github_pr(int(resource_id))
            dt = (time.perf_counter() - t0) * 1000.0
            if pr:
                return AdapterResponse(source_type=self.source_type, app="github", operation="read", success=True, latency_ms=dt, data=pr, resource_reference=f"pr:{resource_id}")
            issue = self.engine.get_github_issue(int(resource_id))
            if issue:
                return AdapterResponse(source_type=self.source_type, app="github", operation="read", success=True, latency_ms=dt, data=issue, resource_reference=f"issue:{resource_id}")
        return AdapterResponse(source_type=self.source_type, app="github", operation="read", success=False, error=f"Resource {resource_id} not found", latency_ms=1.0)

    async def create(
        self,
        resource_type: str,
        payload: Dict[str, Any],
        idempotency_key: str,
        **kwargs
    ) -> tuple[AdapterResponse, ActionReceipt]:
        t0 = time.perf_counter()
        composite_key = idempotency_manager.compute_key("github", f"create_{resource_type}", payload.get("repo", "backend-core"), idempotency_key)

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

        fault = fault_injector.active_fault
        repo_name = payload.get("repo", "backend-core")
        title = payload.get("title", "Bug Report")
        body = payload.get("body", "")
        author = payload.get("author", "reclaim-agent")
        issue_number = 9901

        receipt = ActionReceipt(
            app="github",
            operation=f"create_{resource_type}",
            resource=f"github:{repo_name}:issue:{issue_number}",
            previous_state={},
            requested_state=payload,
            resulting_state={"issue_number": issue_number, "title": title, "repo": repo_name, "state": "open"},
            source_type=self.source_type,
            execution_status="EXECUTED",
            verification_status="PENDING",
            idempotency_key=idempotency_key
        )

        # Fault Injection: WRITE_ACK_WITHOUT_MUTATION
        if fault == "WRITE_ACK_WITHOUT_MUTATION":
            # Return receipt claiming write succeeded, but do not mutate SQLite!
            idempotency_manager.record_receipt(composite_key, receipt)
            resp = AdapterResponse(source_type=self.source_type, app="github", operation=f"create_{resource_type}", success=True, data=receipt.resulting_state, resource_reference=receipt.resource)
            return resp, receipt

        # Mutate State
        self.engine.create_github_issue(
            issue_id=f"issue_{issue_number}",
            repo_name=repo_name,
            number=issue_number,
            title=title,
            body=body,
            author=author,
            labels=payload.get("labels", "reclaim,p0")
        )
        idempotency_manager.record_receipt(composite_key, receipt)
        dt = (time.perf_counter() - t0) * 1000.0
        resp = AdapterResponse(source_type=self.source_type, app="github", operation=f"create_{resource_type}", success=True, latency_ms=dt, data=receipt.resulting_state, resource_reference=receipt.resource)
        return resp, receipt

    async def update(
        self,
        resource_id: str,
        payload: Dict[str, Any],
        idempotency_key: str,
        **kwargs
    ) -> tuple[AdapterResponse, ActionReceipt]:
        # Minimal stub if updating issues
        receipt = ActionReceipt(
            app="github",
            operation="update",
            resource=resource_id,
            requested_state=payload,
            resulting_state=payload,
            source_type=self.source_type,
            execution_status="EXECUTED",
            idempotency_key=idempotency_key
        )
        return AdapterResponse(source_type=self.source_type, app="github", operation="update", success=True, data=payload), receipt

    async def verify(self, action_receipt: ActionReceipt, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        # Parse resource: github:{repo_name}:issue:{issue_number}
        parts = action_receipt.resource.split(":")
        if len(parts) >= 4 and parts[2] == "issue":
            try:
                issue_num = int(parts[3])
                issue = self.engine.get_github_issue(issue_num)
                dt = (time.perf_counter() - t0) * 1000.0
                if issue:
                    req_title = action_receipt.requested_state.get("title")
                    if not req_title or issue.get("title") == req_title:
                        action_receipt.verification_status = "VERIFIED"
                        return AdapterResponse(
                            source_type=self.source_type,
                            app="github",
                            operation="verify",
                            success=True,
                            latency_ms=dt,
                            data={"verified": True, "observed_state": issue}
                        )
            except Exception:
                pass

        action_receipt.verification_status = "FAILED_VERIFICATION"
        dt = (time.perf_counter() - t0) * 1000.0
        return AdapterResponse(
            source_type=self.source_type,
            app="github",
            operation="verify",
            success=False,
            latency_ms=dt,
            error=f"Verification failed: Resource {action_receipt.resource} not found or state mismatch",
            data={"verified": False}
        )
