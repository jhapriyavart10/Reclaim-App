import time
import uuid
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


class SimulatedGmailAdapter(BaseAdapter):
    """
    Simulated Gmail adapter for customer correspondence search, draft creation, and email sending.
    """

    def __init__(self, engine: Optional[WorldStateEngine] = None):
        super().__init__(app_name="gmail", source_type=SourceType.SIMULATED)
        self.engine = engine or world_engine

    async def get_capabilities(self) -> AppCapability:
        return AppCapability(
            app="gmail",
            source_type=SourceType.SIMULATED,
            availability="AVAILABLE",
            authentication_status="AUTHENTICATED",
            read_operations=["search_emails", "get_email"],
            write_operations=["create_draft", "send_email"],
            verification_operations=["verify_email_sent"],
            risk_policy={
                "search_emails": RiskLevel.READ_ONLY,
                "get_email": RiskLevel.READ_ONLY,
                "create_draft": RiskLevel.LOW_RISK_WRITE,
                "send_email": RiskLevel.HIGH_RISK_WRITE  # Customer-facing external action!
            }
        )

    async def search(self, query: str, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        fault = fault_injector.active_fault
        if fault == "NETWORK_TIMEOUT":
            return AdapterResponse(source_type=self.source_type, app="gmail", operation="search", success=False, error="Gmail API timeout", latency_ms=100.0)

        emails = self.engine.search_emails(query=query)
        dt = (time.perf_counter() - t0) * 1000.0
        return AdapterResponse(
            source_type=self.source_type,
            app="gmail",
            operation="search",
            latency_ms=dt,
            success=True,
            data={"emails": emails, "count": len(emails)}
        )

    async def read(self, resource_id: str, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        email = self.engine.get_email_by_id(resource_id)
        dt = (time.perf_counter() - t0) * 1000.0
        if email:
            return AdapterResponse(source_type=self.source_type, app="gmail", operation="read", success=True, latency_ms=dt, data=email, resource_reference=f"gmail:msg:{resource_id}")
        return AdapterResponse(source_type=self.source_type, app="gmail", operation="read", success=False, error=f"Email {resource_id} not found", latency_ms=dt)

    async def create(
        self,
        resource_type: str,
        payload: Dict[str, Any],
        idempotency_key: str,
        **kwargs
    ) -> tuple[AdapterResponse, ActionReceipt]:
        t0 = time.perf_counter()
        to_addr = payload.get("to", "")
        subject = payload.get("subject", "")
        body = payload.get("body", "")
        is_draft = (resource_type == "draft")

        composite_key = idempotency_manager.compute_key("gmail", f"create_{resource_type}", f"{to_addr}:{subject}", idempotency_key)

        if idempotency_manager.has_executed(composite_key):
            cached = idempotency_manager.get_receipt(composite_key)
            resp = AdapterResponse(
                source_type=self.source_type,
                app="gmail",
                operation=f"create_{resource_type}",
                success=True,
                data={"idempotent_replay": True, "result": cached.resulting_state},
                resource_reference=cached.resource
            )
            return resp, cached

        email_id = f"msg_{uuid.uuid4().hex[:8]}"
        thread_id = f"th_{uuid.uuid4().hex[:8]}"

        receipt = ActionReceipt(
            app="gmail",
            operation="send_email" if not is_draft else "create_draft",
            resource=f"gmail:message:{email_id}",
            previous_state={},
            requested_state=payload,
            resulting_state={"email_id": email_id, "to": to_addr, "subject": subject, "is_sent": not is_draft},
            source_type=self.source_type,
            execution_status="EXECUTED",
            verification_status="PENDING",
            idempotency_key=idempotency_key
        )

        fault = fault_injector.active_fault
        if fault == "WRITE_ACK_WITHOUT_MUTATION":
            idempotency_manager.record_receipt(composite_key, receipt)
            resp = AdapterResponse(source_type=self.source_type, app="gmail", operation="send_email", success=True, data=receipt.resulting_state, resource_reference=receipt.resource)
            return resp, receipt

        self.engine.insert_email(
            email_id=email_id,
            thread_id=thread_id,
            from_address="dave@ourcompany.com",
            to_address=to_addr,
            subject=subject,
            body=body,
            is_draft=is_draft,
            is_sent=not is_draft
        )
        idempotency_manager.record_receipt(composite_key, receipt)
        dt = (time.perf_counter() - t0) * 1000.0
        resp = AdapterResponse(source_type=self.source_type, app="gmail", operation="send_email", success=True, latency_ms=dt, data=receipt.resulting_state, resource_reference=receipt.resource)
        return resp, receipt

    async def update(self, resource_id: str, payload: Dict[str, Any], idempotency_key: str, **kwargs) -> tuple[AdapterResponse, ActionReceipt]:
        receipt = ActionReceipt(app="gmail", operation="update", resource=resource_id, source_type=self.source_type, execution_status="NOOP_IDEMPOTENT", idempotency_key=idempotency_key)
        return AdapterResponse(source_type=self.source_type, app="gmail", operation="update", success=True), receipt

    async def verify(self, action_receipt: ActionReceipt, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        email_id = action_receipt.resource.replace("gmail:message:", "")
        email = self.engine.get_email_by_id(email_id)
        dt = (time.perf_counter() - t0) * 1000.0

        if email and email["is_sent"] == 1:
            action_receipt.verification_status = "VERIFIED"
            return AdapterResponse(source_type=self.source_type, app="gmail", operation="verify", success=True, latency_ms=dt, data={"verified": True, "observed_email": email})

        action_receipt.verification_status = "FAILED_VERIFICATION"
        return AdapterResponse(source_type=self.source_type, app="gmail", operation="verify", success=False, latency_ms=dt, error="Verification failed: Email not found in sent box")
