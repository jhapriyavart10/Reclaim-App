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


class SimulatedJiraAdapter(BaseAdapter):
    """
    Simulated Jira adapter operating against the local SQLite World State Engine.
    Supports ticket search, ticket escalation/priority update, and independent verification.
    """

    def __init__(self, engine: Optional[WorldStateEngine] = None):
        super().__init__(app_name="jira", source_type=SourceType.SIMULATED)
        self.engine = engine or world_engine

    async def get_capabilities(self) -> AppCapability:
        return AppCapability(
            app="jira",
            source_type=SourceType.SIMULATED,
            availability="AVAILABLE",
            authentication_status="AUTHENTICATED",
            read_operations=["search_tickets", "get_ticket"],
            write_operations=["update_ticket", "create_ticket"],
            verification_operations=["verify_ticket_fields"],
            risk_policy={
                "search_tickets": RiskLevel.READ_ONLY,
                "get_ticket": RiskLevel.READ_ONLY,
                "update_ticket": RiskLevel.LOW_RISK_WRITE,
                "create_ticket": RiskLevel.LOW_RISK_WRITE
            }
        )

    async def search(self, query: str, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        fault = fault_injector.active_fault
        if fault == "NETWORK_TIMEOUT":
            return AdapterResponse(source_type=self.source_type, app="jira", operation="search", success=False, error="Jira API connection timeout", latency_ms=100.0)
        if fault == "HTTP_503":
            return AdapterResponse(source_type=self.source_type, app="jira", operation="search", success=False, error="Jira API HTTP 503 Service Unavailable", latency_ms=50.0)

        tickets = self.engine.search_jira_tickets(query)
        dt = (time.perf_counter() - t0) * 1000.0
        return AdapterResponse(
            source_type=self.source_type,
            app="jira",
            operation="search",
            latency_ms=dt,
            success=True,
            data={"tickets": tickets, "total": len(tickets)}
        )

    async def read(self, resource_id: str, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        ticket = self.engine.get_jira_ticket(resource_id)
        dt = (time.perf_counter() - t0) * 1000.0
        if ticket:
            return AdapterResponse(
                source_type=self.source_type,
                app="jira",
                operation="read",
                latency_ms=dt,
                success=True,
                data=ticket,
                resource_reference=f"jira:ticket:{resource_id}"
            )
        return AdapterResponse(source_type=self.source_type, app="jira", operation="read", latency_ms=dt, success=False, error=f"Ticket {resource_id} not found")

    async def create(self, resource_type: str, payload: Dict[str, Any], idempotency_key: str, **kwargs) -> tuple[AdapterResponse, ActionReceipt]:
        t0 = time.perf_counter()
        composite_key = idempotency_manager.compute_key("jira", "create_ticket", "new", idempotency_key)
        if idempotency_manager.has_executed(composite_key):
            cached = idempotency_manager.get_receipt(composite_key)
            return AdapterResponse(source_type=self.source_type, app="jira", operation="create_ticket", success=True, data={"idempotent_replay": True, "result": cached.resulting_state}, resource_reference=cached.resource), cached

        ticket_key = payload.get("key") or f"PROD-{uuid.uuid4().hex[:4].upper()}"
        summary = payload.get("summary", "New Incident Ticket")
        description = payload.get("description", "Created by RECLAIM agent")
        priority = payload.get("priority", "High")
        assignee = payload.get("assignee", "oncall")
        reporter = payload.get("reporter", "reclaim_agent")
        status = payload.get("status", "Open")

        self.engine.create_jira_ticket(
            key=ticket_key,
            summary=summary,
            description=description,
            priority=priority,
            status=status,
            assignee=assignee,
            reporter=reporter
        )
        dt = (time.perf_counter() - t0) * 1000.0
        receipt = ActionReceipt(
            app="jira",
            operation="create_ticket",
            resource=f"jira:ticket:{ticket_key}",
            previous_state={},
            requested_state=payload,
            resulting_state={"key": ticket_key, "summary": summary, "priority": priority, "status": status},
            source_type=self.source_type,
            execution_status="EXECUTED",
            verification_status="PENDING",
            idempotency_key=idempotency_key
        )
        idempotency_manager.record_receipt(composite_key, receipt)
        return AdapterResponse(source_type=self.source_type, app="jira", operation="create_ticket", latency_ms=dt, success=True, data=receipt.resulting_state, resource_reference=receipt.resource), receipt

    async def update(
        self,
        resource_id: str,
        payload: Dict[str, Any],
        idempotency_key: str,
        **kwargs
    ) -> tuple[AdapterResponse, ActionReceipt]:
        t0 = time.perf_counter()
        ticket_key = resource_id
        candidate_key = payload.get("key") or payload.get("issue_id") or payload.get("ticket_id") or payload.get("ticket_key")
        if candidate_key and ("PROD-" in str(candidate_key) or "DB-" in str(candidate_key) or "-" in str(candidate_key)):
            ticket_key = str(candidate_key)
        elif not ("-" in str(ticket_key)) and candidate_key:
            ticket_key = str(candidate_key)

        composite_key = idempotency_manager.compute_key("jira", "update_ticket", ticket_key, idempotency_key)

        # Idempotency Check
        if idempotency_manager.has_executed(composite_key):
            cached = idempotency_manager.get_receipt(composite_key)
            resp = AdapterResponse(
                source_type=self.source_type,
                app="jira",
                operation="update_ticket",
                success=True,
                data={"idempotent_replay": True, "result": cached.resulting_state},
                resource_reference=cached.resource
            )
            return resp, cached

        current_ticket = self.engine.get_jira_ticket(ticket_key) or {}
        previous_state = {k: current_ticket.get(k) for k in payload.keys() if k in current_ticket}

        resulting_state = dict(current_ticket)
        resulting_state.update(payload)

        receipt = ActionReceipt(
            app="jira",
            operation="update_ticket",
            resource=f"jira:ticket:{ticket_key}",
            previous_state=previous_state,
            requested_state=payload,
            resulting_state=resulting_state,
            source_type=self.source_type,
            execution_status="EXECUTED",
            verification_status="PENDING",
            idempotency_key=idempotency_key
        )

        fault = fault_injector.active_fault
        # Fault: WRITE_ACK_WITHOUT_MUTATION
        if fault == "WRITE_ACK_WITHOUT_MUTATION":
            idempotency_manager.record_receipt(composite_key, receipt)
            resp = AdapterResponse(source_type=self.source_type, app="jira", operation="update_ticket", success=True, data=receipt.resulting_state, resource_reference=receipt.resource)
            return resp, receipt

        self.engine.update_jira_ticket(ticket_key, payload)
        idempotency_manager.record_receipt(composite_key, receipt)
        dt = (time.perf_counter() - t0) * 1000.0
        resp = AdapterResponse(source_type=self.source_type, app="jira", operation="update_ticket", success=True, latency_ms=dt, data=receipt.resulting_state, resource_reference=receipt.resource)
        return resp, receipt

    async def verify(self, action_receipt: ActionReceipt, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        ticket_key = action_receipt.resource.replace("jira:ticket:", "")
        # Independent read query to destination system
        current_ticket = self.engine.get_jira_ticket(ticket_key)
        dt = (time.perf_counter() - t0) * 1000.0

        if not current_ticket:
            action_receipt.verification_status = "FAILED_VERIFICATION"
            return AdapterResponse(source_type=self.source_type, app="jira", operation="verify", success=False, latency_ms=dt, error=f"Ticket {ticket_key} not found")

        # Verify all requested fields match observed state
        mismatches = {}
        valid_columns = {"summary", "description", "priority", "status", "assignee", "reporter"}

        # Flatten requested state if nested
        check_fields = {}
        for k, v in (action_receipt.requested_state or {}).items():
            if k == "fields" and isinstance(v, dict):
                for fk, fv in v.items():
                    check_fields[fk] = fv
            elif k in ("key", "issue_id", "ticket_id", "ticket_key"):
                check_fields["key"] = str(v)
            elif k in valid_columns and not isinstance(v, (dict, list)):
                check_fields[k] = str(v)

        for field, expected_val in check_fields.items():
            actual_val = current_ticket.get(field)
            if actual_val != expected_val:
                mismatches[field] = {"expected": expected_val, "actual": actual_val}

        if not mismatches:
            action_receipt.verification_status = "VERIFIED"
            return AdapterResponse(
                source_type=self.source_type,
                app="jira",
                operation="verify",
                success=True,
                latency_ms=dt,
                data={"verified": True, "observed_ticket": current_ticket}
            )

        action_receipt.verification_status = "FAILED_VERIFICATION"
        return AdapterResponse(
            source_type=self.source_type,
            app="jira",
            operation="verify",
            success=False,
            latency_ms=dt,
            error=f"Verification failed: State mismatch on fields {mismatches}",
            data={"verified": False, "mismatches": mismatches}
        )
