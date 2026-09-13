import os
from typing import Optional, Literal

FaultType = Literal[
    "NONE",
    "NETWORK_TIMEOUT",
    "HTTP_503",
    "STALE_READ",
    "WRITE_ACK_WITHOUT_MUTATION",
    "DUPLICATE_REQUEST",
    "CALENDAR_CONFLICT"
]


class FaultInjector:
    """
    Manages deterministic fault injection for testing resilience and verification failure detection.
    Can be configured programmatically or via the FAULT_INJECTOR environment variable.
    """

    def __init__(self):
        self._active_fault: FaultType = "NONE"

    @property
    def active_fault(self) -> FaultType:
        env_fault = os.getenv("FAULT_INJECTOR", "").upper()
        if env_fault in [
            "NETWORK_TIMEOUT",
            "HTTP_503",
            "STALE_READ",
            "WRITE_ACK_WITHOUT_MUTATION",
            "DUPLICATE_REQUEST",
            "CALENDAR_CONFLICT"
        ]:
            return env_fault  # type: ignore
        return self._active_fault

    def set_fault(self, fault: FaultType) -> None:
        self._active_fault = fault

    def clear(self) -> None:
        self._active_fault = "NONE"
        if "FAULT_INJECTOR" in os.environ:
            del os.environ["FAULT_INJECTOR"]


# Global singleton fault injector
fault_injector = FaultInjector()
