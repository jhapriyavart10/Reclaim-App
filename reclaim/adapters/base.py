import abc
import enum
import time
import uuid
from typing import Dict, Any, Optional, List, Literal
from pydantic import BaseModel, Field


class SourceType(str, enum.Enum):
    LIVE = "LIVE"
    SIMULATED = "SIMULATED"


class ConnectorStatus(str, enum.Enum):
    AVAILABLE = "AVAILABLE"
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"
    LIVE_VERIFIED = "LIVE_VERIFIED"
    SIMULATED = "SIMULATED"


class RiskLevel(str, enum.Enum):
    READ_ONLY = "READ_ONLY"
    LOW_RISK_WRITE = "LOW_RISK_WRITE"
    HIGH_RISK_WRITE = "HIGH_RISK_WRITE"


class AdapterResponse(BaseModel):
    """
    Normalized response returned by all external app adapters (Live or Simulated).
    Core agent consumes ONLY this normalized schema.
    """
    source_type: SourceType = Field(description="Explicit provenance: LIVE or SIMULATED")
    app: str = Field(description="Application name (e.g., 'github', 'slack', 'jira', 'gmail')")
    operation: str = Field(description="Executed operation (e.g., 'search', 'read', 'create', 'update', 'verify')")
    request_id: str = Field(default_factory=lambda: str(uuid.uuid4()), description="Traceable request ID")
    timestamp: str = Field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    latency_ms: float = Field(default=0.0, description="Measured execution latency")
    success: bool = Field(description="Whether the operation succeeded")
    data: Any = Field(default=None, description="Normalized response payload")
    error: Optional[str] = Field(default=None, description="Sanitized error description if failed")
    resource_reference: Optional[str] = Field(default=None, description="ID or URI of mutated/queried resource")
    raw_provider_metadata: Optional[Dict[str, Any]] = Field(default=None, description="Safe optional metadata")
    connector_status: Optional[ConnectorStatus] = Field(default=None, description="Granular connector live status")


class ActionReceipt(BaseModel):
    """
    Tamper-evident audit receipt generated for every state mutation.
    Ensures writes can be independently inspected and verified.
    """
    action_id: str = Field(default_factory=lambda: f"act_{uuid.uuid4().hex[:8]}")
    app: str
    operation: str
    resource: str
    previous_state: Dict[str, Any] = Field(default_factory=dict)
    requested_state: Dict[str, Any] = Field(default_factory=dict)
    resulting_state: Dict[str, Any] = Field(default_factory=dict)
    source_type: SourceType
    execution_status: Literal["EXECUTED", "REJECTED", "FAILED", "NOOP_IDEMPOTENT"]
    verification_status: Literal["PENDING", "VERIFIED", "FAILED_VERIFICATION", "UNVERIFIED"] = "PENDING"
    timestamp: str = Field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    idempotency_key: str
    error: Optional[str] = None
    connector_status: Optional[ConnectorStatus] = None


class AppCapability(BaseModel):
    """
    Describes the discovered capability and safety profile of an application adapter.
    """
    app: str
    source_type: SourceType
    availability: Literal["AVAILABLE", "UNAVAILABLE", "DEGRADED"]
    authentication_status: Literal["AUTHENTICATED", "UNCONFIGURED", "INVALID_CREDENTIALS"]
    read_operations: List[str]
    write_operations: List[str]
    verification_operations: List[str]
    risk_policy: Dict[str, RiskLevel]
    connector_status: ConnectorStatus = ConnectorStatus.AVAILABLE


class BaseAdapter(abc.ABC):
    """
    Abstract contract for all application adapters.
    Guarantees vendor-neutral interaction for both LIVE and SIMULATED implementations.
    """

    def __init__(self, app_name: str, source_type: SourceType):
        self.app_name = app_name
        self.source_type = source_type

    @abc.abstractmethod
    async def get_capabilities(self) -> AppCapability:
        """Return declared capabilities, availability, and risk tiers."""
        pass

    @abc.abstractmethod
    async def search(self, query: str, **kwargs) -> AdapterResponse:
        """Search records, messages, or entities within the target application."""
        pass

    @abc.abstractmethod
    async def read(self, resource_id: str, **kwargs) -> AdapterResponse:
        """Read a specific entity or thread by its identifier."""
        pass

    @abc.abstractmethod
    async def create(
        self,
        resource_type: str,
        payload: Dict[str, Any],
        idempotency_key: str,
        **kwargs
    ) -> tuple[AdapterResponse, ActionReceipt]:
        """Create a new resource with idempotency protection and audit receipt generation."""
        pass

    @abc.abstractmethod
    async def update(
        self,
        resource_id: str,
        payload: Dict[str, Any],
        idempotency_key: str,
        **kwargs
    ) -> tuple[AdapterResponse, ActionReceipt]:
        """Update an existing resource with idempotency protection and audit receipt generation."""
        pass

    @abc.abstractmethod
    async def verify(self, action_receipt: ActionReceipt, **kwargs) -> AdapterResponse:
        """Independently query destination system to verify that the mutation actually took effect."""
        pass
