import re
import time
import uuid
from typing import Dict, Any, List, Optional, Set
from pydantic import BaseModel, Field, model_validator

from reclaim.adapters.base import SourceType, ConnectorStatus


class EvidenceItem(BaseModel):
    """Normalized atomic evidence node extracted from tool responses."""
    evidence_id: str = Field(default_factory=lambda: f"ev_{uuid.uuid4().hex[:6]}")
    app: str
    source_type: SourceType
    resource_reference: str
    timestamp: str
    author: str
    content: str
    entities: List[str] = Field(default_factory=list)
    relevance: float = 1.0
    retrieved_at: str = Field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    raw_data: Dict[str, Any] = Field(default_factory=dict)
    connector_status: ConnectorStatus = ConnectorStatus.SIMULATED

    @model_validator(mode="after")
    def validate_provenance(self) -> 'EvidenceItem':
        if self.source_type == SourceType.SIMULATED:
            if self.connector_status in (ConnectorStatus.LIVE_VERIFIED, "LIVE_VERIFIED", "LIVE"):
                raise ValueError("Integrity violation: Simulated evidence cannot be labeled LIVE or LIVE_VERIFIED.")
        return self


class EntityNormalizer:
    """
    Deterministic normalizer linking variations of customer, ticket, and PR references.
    """
    @staticmethod
    def normalize_customer(name: str) -> str:
        clean = name.strip()
        if re.search(r"acme", clean, re.IGNORECASE):
            return "Acme Corp"
        return clean

    @staticmethod
    def extract_entities(text: str) -> List[str]:
        entities = []
        # Tickets like PROD-1042
        for m in re.findall(r"\b[A-Z]{3,5}-\d+\b", text):
            entities.append(f"TICKET:{m}")
        # PRs like PR #882 or #882
        for m in re.findall(r"(?:PR\s*#?|#)(\d{2,5})\b", text):
            entities.append(f"PULL_REQUEST:{m}")
        # Endpoints like /v2/data-sync
        for m in re.findall(r"/v\d+/[a-zA-Z0-9_\-]+", text):
            entities.append(f"ENDPOINT:{m}")
        # Customer references
        if re.search(r"\bacme\b", text, re.IGNORECASE):
            entities.append("CUSTOMER:Acme Corp")
        return list(set(entities))


class EvidenceStore:
    """
    Mission-level repository holding verified, normalized evidence items.
    Enforces the anti-hallucination barrier by rejecting non-existent evidence IDs.
    """

    def __init__(self):
        self._items: Dict[str, EvidenceItem] = {}
        self._counter: int = 1

    def add_evidence(
        self,
        app: str,
        source_type: SourceType,
        resource_reference: str,
        timestamp: str,
        author: str,
        content: str,
        raw_data: Optional[Dict[str, Any]] = None,
        custom_id: Optional[str] = None,
        connector_status: Optional[ConnectorStatus] = None
    ) -> EvidenceItem:
        ev_id = custom_id or f"ev_{app}_{self._counter:02d}"
        self._counter += 1

        if connector_status is None:
            c_status = ConnectorStatus.LIVE_VERIFIED if source_type == SourceType.LIVE else ConnectorStatus.SIMULATED
        else:
            c_status = connector_status

        entities = EntityNormalizer.extract_entities(content)
        item = EvidenceItem(
            evidence_id=ev_id,
            app=app,
            source_type=source_type,
            resource_reference=resource_reference,
            timestamp=timestamp,
            author=author,
            content=content,
            entities=entities,
            raw_data=raw_data or {},
            connector_status=c_status
        )
        self._items[ev_id] = item
        return item

    def add_item(self, item: EvidenceItem) -> EvidenceItem:
        self._items[item.evidence_id] = item
        return item

    def get(self, evidence_id: str) -> Optional[EvidenceItem]:
        return self._items.get(evidence_id)

    def get_all(self) -> List[EvidenceItem]:
        return list(self._items.values())

    def validate_ids(self, ids: List[str]) -> bool:
        """
        Anti-hallucination check: Confirms every claimed evidence ID exists in the store.
        """
        if not ids:
            return False
        return all(eid in self._items for eid in ids)

    def get_corroborating_apps(self, ids: List[str]) -> Set[str]:
        """
        Returns the set of distinct apps for a given list of evidence IDs.
        """
        apps = set()
        for eid in ids:
            item = self.get(eid)
            if item:
                apps.add(item.app)
        return apps

    def clear(self) -> None:
        self._items.clear()
        self._counter = 1
