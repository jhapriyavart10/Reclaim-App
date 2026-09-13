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


class SimulatedCalendarAdapter(BaseAdapter):
    """
    Simulated Google Calendar adapter for availability lookup, executive meeting creation, and verification.
    """

    def __init__(self, engine: Optional[WorldStateEngine] = None):
        super().__init__(app_name="calendar", source_type=SourceType.SIMULATED)
        self.engine = engine or world_engine

    async def get_capabilities(self) -> AppCapability:
        return AppCapability(
            app="calendar",
            source_type=SourceType.SIMULATED,
            availability="AVAILABLE",
            authentication_status="AUTHENTICATED",
            read_operations=["check_availability", "search_events"],
            write_operations=["create_meeting"],
            verification_operations=["verify_event_scheduled"],
            risk_policy={
                "check_availability": RiskLevel.READ_ONLY,
                "search_events": RiskLevel.READ_ONLY,
                "create_meeting": RiskLevel.HIGH_RISK_WRITE  # External customer-facing meeting booking!
            }
        )

    async def search(self, query: str, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        events = self.engine.search_calendar_events(query=query)
        dt = (time.perf_counter() - t0) * 1000.0
        return AdapterResponse(source_type=self.source_type, app="calendar", operation="search", success=True, latency_ms=dt, data={"events": events})

    async def read(self, resource_id: str, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        event = self.engine.get_calendar_event(resource_id)
        dt = (time.perf_counter() - t0) * 1000.0
        if event:
            return AdapterResponse(source_type=self.source_type, app="calendar", operation="read", success=True, latency_ms=dt, data=event, resource_reference=f"calendar:event:{resource_id}")
        return AdapterResponse(source_type=self.source_type, app="calendar", operation="read", success=False, error=f"Event {resource_id} not found", latency_ms=dt)

    async def create(
        self,
        resource_type: str,
        payload: Dict[str, Any],
        idempotency_key: str,
        **kwargs
    ) -> tuple[AdapterResponse, ActionReceipt]:
        t0 = time.perf_counter()
        summary = payload.get("summary", "Executive Alignment")
        start_time = payload.get("start_time", "2026-09-14T14:00:00Z")
        end_time = payload.get("end_time", "2026-09-14T14:30:00Z")
        attendees = payload.get("attendees", "sarah@acme.com")

        composite_key = idempotency_manager.compute_key("calendar", "create_meeting", f"{summary}:{start_time}", idempotency_key)

        if idempotency_manager.has_executed(composite_key):
            cached = idempotency_manager.get_receipt(composite_key)
            resp = AdapterResponse(
                source_type=self.source_type,
                app="calendar",
                operation="create_meeting",
                success=True,
                data={"idempotent_replay": True, "result": cached.resulting_state},
                resource_reference=cached.resource
            )
            return resp, cached

        fault = fault_injector.active_fault
        if fault == "CALENDAR_CONFLICT":
            receipt = ActionReceipt(
                app="calendar",
                operation="create_meeting",
                resource="calendar:conflict",
                source_type=self.source_type,
                execution_status="FAILED",
                error="Slot already booked: Double-booking detected",
                idempotency_key=idempotency_key
            )
            return AdapterResponse(source_type=self.source_type, app="calendar", operation="create_meeting", success=False, error="Slot conflict", latency_ms=50.0), receipt

        event_id = f"evt_{uuid.uuid4().hex[:8]}"
        receipt = ActionReceipt(
            app="calendar",
            operation="create_meeting",
            resource=f"calendar:event:{event_id}",
            previous_state={},
            requested_state=payload,
            resulting_state={"event_id": event_id, "summary": summary, "start_time": start_time, "attendees": attendees},
            source_type=self.source_type,
            execution_status="EXECUTED",
            verification_status="PENDING",
            idempotency_key=idempotency_key
        )

        if fault == "WRITE_ACK_WITHOUT_MUTATION":
            idempotency_manager.record_receipt(composite_key, receipt)
            resp = AdapterResponse(source_type=self.source_type, app="calendar", operation="create_meeting", success=True, data=receipt.resulting_state, resource_reference=receipt.resource)
            return resp, receipt

        self.engine.insert_calendar_event(
            event_id=event_id,
            summary=summary,
            description=payload.get("description", "RECLAIM Incident Review"),
            start_time=start_time,
            end_time=end_time,
            attendees=attendees
        )
        idempotency_manager.record_receipt(composite_key, receipt)
        dt = (time.perf_counter() - t0) * 1000.0
        resp = AdapterResponse(source_type=self.source_type, app="calendar", operation="create_meeting", success=True, latency_ms=dt, data=receipt.resulting_state, resource_reference=receipt.resource)
        return resp, receipt

    async def update(self, resource_id: str, payload: Dict[str, Any], idempotency_key: str, **kwargs) -> tuple[AdapterResponse, ActionReceipt]:
        receipt = ActionReceipt(app="calendar", operation="update", resource=resource_id, source_type=self.source_type, execution_status="NOOP_IDEMPOTENT", idempotency_key=idempotency_key)
        return AdapterResponse(source_type=self.source_type, app="calendar", operation="update", success=True), receipt

    async def verify(self, action_receipt: ActionReceipt, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        event_id = action_receipt.resource.replace("calendar:event:", "")
        event = self.engine.get_calendar_event(event_id)
        dt = (time.perf_counter() - t0) * 1000.0

        if event:
            action_receipt.verification_status = "VERIFIED"
            return AdapterResponse(source_type=self.source_type, app="calendar", operation="verify", success=True, latency_ms=dt, data={"verified": True, "observed_event": event})

        action_receipt.verification_status = "FAILED_VERIFICATION"
        return AdapterResponse(source_type=self.source_type, app="calendar", operation="verify", success=False, latency_ms=dt, error=f"Verification failed: Event {event_id} not found in calendar")
