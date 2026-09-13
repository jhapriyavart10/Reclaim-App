import time
from typing import Dict, Any, Optional
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


class SimulatedCRMAdapter(BaseAdapter):
    """
    Simulated CRM adapter for account health queries, ARR lookup, and status updates.
    """

    def __init__(self, engine: Optional[WorldStateEngine] = None):
        super().__init__(app_name="crm", source_type=SourceType.SIMULATED)
        self.engine = engine or world_engine

    async def get_capabilities(self) -> AppCapability:
        return AppCapability(
            app="crm",
            source_type=SourceType.SIMULATED,
            availability="AVAILABLE",
            authentication_status="AUTHENTICATED",
            read_operations=["get_account", "search_accounts"],
            write_operations=["update_health_score"],
            verification_operations=["verify_account_health"],
            risk_policy={
                "get_account": RiskLevel.READ_ONLY,
                "search_accounts": RiskLevel.READ_ONLY,
                "update_health_score": RiskLevel.LOW_RISK_WRITE
            }
        )

    async def search(self, query: str, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        account = self.engine.get_crm_account(query)
        dt = (time.perf_counter() - t0) * 1000.0
        return AdapterResponse(source_type=self.source_type, app="crm", operation="search", success=True, latency_ms=dt, data={"accounts": [account] if account else []})

    async def read(self, resource_id: str, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        account = self.engine.get_crm_account(resource_id)
        dt = (time.perf_counter() - t0) * 1000.0
        if account:
            return AdapterResponse(source_type=self.source_type, app="crm", operation="read", success=True, latency_ms=dt, data=account, resource_reference=f"crm:account:{account['id']}")
        return AdapterResponse(source_type=self.source_type, app="crm", operation="read", success=False, latency_ms=dt, error=f"CRM Account {resource_id} not found")

    async def create(self, resource_type: str, payload: Dict[str, Any], idempotency_key: str, **kwargs) -> tuple[AdapterResponse, ActionReceipt]:
        receipt = ActionReceipt(app="crm", operation="create", resource="crm:account", source_type=self.source_type, execution_status="NOOP_IDEMPOTENT", idempotency_key=idempotency_key)
        return AdapterResponse(source_type=self.source_type, app="crm", operation="create", success=True), receipt

    async def update(
        self,
        resource_id: str,
        payload: Dict[str, Any],
        idempotency_key: str,
        **kwargs
    ) -> tuple[AdapterResponse, ActionReceipt]:
        t0 = time.perf_counter()
        composite_key = idempotency_manager.compute_key("crm", "update_health", resource_id, idempotency_key)

        if idempotency_manager.has_executed(composite_key):
            cached = idempotency_manager.get_receipt(composite_key)
            resp = AdapterResponse(source_type=self.source_type, app="crm", operation="update", success=True, data={"idempotent_replay": True, "result": cached.resulting_state}, resource_reference=cached.resource)
            return resp, cached

        account = self.engine.get_crm_account(resource_id) or {}
        new_health = payload.get("health_score", 50)
        status = payload.get("status")

        receipt = ActionReceipt(
            app="crm",
            operation="update_health",
            resource=f"crm:account:{resource_id}",
            previous_state={"health_score": account.get("health_score")},
            requested_state=payload,
            resulting_state={"health_score": new_health, "status": status},
            source_type=self.source_type,
            execution_status="EXECUTED",
            verification_status="PENDING",
            idempotency_key=idempotency_key
        )

        fault = fault_injector.active_fault
        if fault == "WRITE_ACK_WITHOUT_MUTATION":
            idempotency_manager.record_receipt(composite_key, receipt)
            resp = AdapterResponse(source_type=self.source_type, app="crm", operation="update", success=True, data=receipt.resulting_state, resource_reference=receipt.resource)
            return resp, receipt

        self.engine.update_crm_account_health(account_id=account.get("id", resource_id), health_score=new_health, status=status)
        idempotency_manager.record_receipt(composite_key, receipt)
        dt = (time.perf_counter() - t0) * 1000.0
        resp = AdapterResponse(source_type=self.source_type, app="crm", operation="update", success=True, latency_ms=dt, data=receipt.resulting_state, resource_reference=receipt.resource)
        return resp, receipt

    async def verify(self, action_receipt: ActionReceipt, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        account_id = action_receipt.resource.replace("crm:account:", "")
        account = self.engine.get_crm_account(account_id)
        dt = (time.perf_counter() - t0) * 1000.0

        if account and account["health_score"] == action_receipt.requested_state.get("health_score"):
            action_receipt.verification_status = "VERIFIED"
            return AdapterResponse(source_type=self.source_type, app="crm", operation="verify", success=True, latency_ms=dt, data={"verified": True, "observed_account": account})

        action_receipt.verification_status = "FAILED_VERIFICATION"
        return AdapterResponse(source_type=self.source_type, app="crm", operation="verify", success=False, latency_ms=dt, error="Verification failed: CRM health score mismatch")
