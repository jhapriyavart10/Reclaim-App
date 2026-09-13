import hashlib
import json
from typing import Dict, Any, Optional
from reclaim.adapters.base import ActionReceipt, SourceType


class IdempotencyManager:
    """
    In-memory and persistent registry tracking executed action idempotency keys.
    Prevents duplicate writes, double-posting, or redundant ticket creation.
    """

    def __init__(self):
        self._executed_receipts: Dict[str, ActionReceipt] = {}

    def compute_key(self, app: str, operation: str, resource: str, idempotency_key: str) -> str:
        raw = f"{app}:{operation}:{resource}:{idempotency_key}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def has_executed(self, composite_key: str) -> bool:
        return composite_key in self._executed_receipts

    def get_receipt(self, composite_key: str) -> Optional[ActionReceipt]:
        return self._executed_receipts.get(composite_key)

    def record_receipt(self, composite_key: str, receipt: ActionReceipt) -> None:
        self._executed_receipts[composite_key] = receipt

    def clear(self) -> None:
        self._executed_receipts.clear()


# Global singleton instance
idempotency_manager = IdempotencyManager()
