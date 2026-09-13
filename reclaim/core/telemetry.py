import time
import uuid
from typing import Dict, Any, Optional, List
from pydantic import BaseModel, Field


class TelemetryEvent(BaseModel):
    """Structured, operational event emitted during mission lifecycle."""
    event_id: str = Field(default_factory=lambda: f"evt_{uuid.uuid4().hex[:8]}")
    event_type: str
    mission_id: str
    timestamp: str = Field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    state: str
    step_id: Optional[str] = None
    app: Optional[str] = None
    source_type: Optional[str] = None
    summary: str
    details: Dict[str, Any] = Field(default_factory=dict)


class TelemetryBus:
    """
    In-memory observable event bus collecting audit events.
    Exposes clean operational telemetry stream without dumping raw LLM thoughts.
    """

    def __init__(self):
        self._events: List[TelemetryEvent] = []

    def emit(
        self,
        event_type: str,
        mission_id: str,
        state: str,
        summary: str,
        step_id: Optional[str] = None,
        app: Optional[str] = None,
        source_type: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ) -> TelemetryEvent:
        event = TelemetryEvent(
            event_type=event_type,
            mission_id=mission_id,
            state=state,
            summary=summary,
            step_id=step_id,
            app=app,
            source_type=source_type,
            details=details or {}
        )
        self._events.append(event)
        return event

    def get_events(self, mission_id: Optional[str] = None) -> List[TelemetryEvent]:
        if mission_id:
            return [e for e in self._events if e.mission_id == mission_id]
        return list(self._events)

    def clear(self) -> None:
        self._events.clear()


# Default singleton telemetry bus
telemetry_bus = TelemetryBus()
