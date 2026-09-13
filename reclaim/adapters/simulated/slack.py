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


class SimulatedSlackAdapter(BaseAdapter):
    """
    Simulated Slack adapter operating against the local SQLite World State Engine.
    Exposes channel message search, message posting, and history verification.
    """

    def __init__(self, engine: Optional[WorldStateEngine] = None):
        super().__init__(app_name="slack", source_type=SourceType.SIMULATED)
        self.engine = engine or world_engine

    async def get_capabilities(self) -> AppCapability:
        return AppCapability(
            app="slack",
            source_type=SourceType.SIMULATED,
            availability="AVAILABLE",
            authentication_status="AUTHENTICATED",
            read_operations=["search_messages", "read_channel_history"],
            write_operations=["post_message"],
            verification_operations=["verify_message_posted"],
            risk_policy={
                "search_messages": RiskLevel.READ_ONLY,
                "read_channel_history": RiskLevel.READ_ONLY,
                "post_message": RiskLevel.LOW_RISK_WRITE
            }
        )

    async def search(self, query: str, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        fault = fault_injector.active_fault
        if fault == "NETWORK_TIMEOUT":
            return AdapterResponse(source_type=self.source_type, app="slack", operation="search", success=False, error="Slack API connection timeout", latency_ms=100.0)
        if fault == "HTTP_503":
            return AdapterResponse(source_type=self.source_type, app="slack", operation="search", success=False, error="Slack API HTTP 503 Service Unavailable", latency_ms=50.0)

        channel_name = kwargs.get("channel_name")
        messages = self.engine.search_slack_messages(query=query, channel_name=channel_name)
        dt = (time.perf_counter() - t0) * 1000.0
        return AdapterResponse(
            source_type=self.source_type,
            app="slack",
            operation="search",
            latency_ms=dt,
            success=True,
            data={"messages": messages, "count": len(messages)}
        )

    async def read(self, resource_id: str, **kwargs) -> AdapterResponse:
        # Read messages for a specific channel
        t0 = time.perf_counter()
        messages = self.engine.search_slack_messages(query="", channel_name=resource_id)
        dt = (time.perf_counter() - t0) * 1000.0
        return AdapterResponse(
            source_type=self.source_type,
            app="slack",
            operation="read",
            latency_ms=dt,
            success=True,
            data={"channel": resource_id, "messages": messages}
        )

    async def create(
        self,
        resource_type: str,
        payload: Dict[str, Any],
        idempotency_key: str,
        **kwargs
    ) -> tuple[AdapterResponse, ActionReceipt]:
        t0 = time.perf_counter()
        channel = payload.get("channel", "alerts-enterprise")
        text = payload.get("text") or payload.get("message") or payload.get("content") or ""
        user = payload.get("user", "reclaim-bot")
        composite_key = idempotency_manager.compute_key("slack", "post_message", channel, idempotency_key)

        # Idempotency Check
        if idempotency_manager.has_executed(composite_key):
            cached = idempotency_manager.get_receipt(composite_key)
            resp = AdapterResponse(
                source_type=self.source_type,
                app="slack",
                operation="post_message",
                success=True,
                data={"idempotent_replay": True, "result": cached.resulting_state},
                resource_reference=cached.resource
            )
            return resp, cached

        fault = fault_injector.active_fault
        msg_id = f"msg_{uuid.uuid4().hex[:8]}"
        receipt = ActionReceipt(
            app="slack",
            operation="post_message",
            resource=f"slack:channel:{channel}:msg:{msg_id}",
            previous_state={},
            requested_state={"channel": channel, "text": text, "user": user, **payload},
            resulting_state={"msg_id": msg_id, "channel": channel, "text": text, "user": user},
            source_type=self.source_type,
            execution_status="EXECUTED",
            verification_status="PENDING",
            idempotency_key=idempotency_key
        )

        # Fault Injection: WRITE_ACK_WITHOUT_MUTATION
        if fault == "WRITE_ACK_WITHOUT_MUTATION":
            idempotency_manager.record_receipt(composite_key, receipt)
            resp = AdapterResponse(source_type=self.source_type, app="slack", operation="post_message", success=True, data=receipt.resulting_state, resource_reference=receipt.resource)
            return resp, receipt

        self.engine.insert_slack_message(msg_id=msg_id, channel_name=channel, user=user, text=text)
        idempotency_manager.record_receipt(composite_key, receipt)
        dt = (time.perf_counter() - t0) * 1000.0
        resp = AdapterResponse(source_type=self.source_type, app="slack", operation="post_message", success=True, latency_ms=dt, data=receipt.resulting_state, resource_reference=receipt.resource)
        return resp, receipt

    async def update(self, resource_id: str, payload: Dict[str, Any], idempotency_key: str, **kwargs) -> tuple[AdapterResponse, ActionReceipt]:
        receipt = ActionReceipt(app="slack", operation="update", resource=resource_id, source_type=self.source_type, execution_status="NOOP_IDEMPOTENT", idempotency_key=idempotency_key)
        return AdapterResponse(source_type=self.source_type, app="slack", operation="update", success=True), receipt

    async def verify(self, action_receipt: ActionReceipt, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        req_state = action_receipt.requested_state or {}
        res_state = action_receipt.resulting_state or {}
        channel = req_state.get("channel") or res_state.get("channel")
        text = req_state.get("text") or res_state.get("text") or req_state.get("message") or ""

        # Independent read query to check if message exists in channel history
        query_sub = text[:25] if text else ""
        history = self.engine.search_slack_messages(query=query_sub, channel_name=channel)
        dt = (time.perf_counter() - t0) * 1000.0

        for msg in history:
            if not text or msg.get("text") == text or (text and text[:25] in msg.get("text", "")):
                action_receipt.verification_status = "VERIFIED"
                return AdapterResponse(
                    source_type=self.source_type,
                    app="slack",
                    operation="verify",
                    success=True,
                    latency_ms=dt,
                    data={"verified": True, "observed_message": msg}
                )

        action_receipt.verification_status = "FAILED_VERIFICATION"
        return AdapterResponse(
            source_type=self.source_type,
            app="slack",
            operation="verify",
            success=False,
            latency_ms=dt,
            error=f"Verification failed: Message not found in channel '{channel}'",
            data={"verified": False}
        )
