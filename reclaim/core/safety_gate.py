import uuid
import logging
from typing import Dict, Optional, Literal, Tuple, List

from reclaim.adapters.base import RiskLevel
from reclaim.llm.schemas import ActionProposal, ApprovalRequest

logger = logging.getLogger(__name__)

# Deterministic safety policy mapping: (app, operation) -> RiskLevel
DETERMINISTIC_RISK_POLICY: Dict[Tuple[str, str], RiskLevel] = {
    # High Risk: Customer-facing, irreversible, or contractual actions
    ("gmail", "send_email"): RiskLevel.HIGH_RISK_WRITE,
    ("calendar", "create_meeting"): RiskLevel.HIGH_RISK_WRITE,
    ("github", "revert_pull_request"): RiskLevel.HIGH_RISK_WRITE,

    # Low Risk: Internal, reversible, or triage actions
    ("jira", "update_issue"): RiskLevel.LOW_RISK_WRITE,
    ("jira", "update_ticket"): RiskLevel.LOW_RISK_WRITE,
    ("jira", "create_ticket"): RiskLevel.LOW_RISK_WRITE,
    ("slack", "post_message"): RiskLevel.LOW_RISK_WRITE,
    ("github", "create_issue"): RiskLevel.LOW_RISK_WRITE,
    ("github", "create_comment"): RiskLevel.LOW_RISK_WRITE,
    ("crm", "update_health"): RiskLevel.LOW_RISK_WRITE,
    ("crm", "update_health_score"): RiskLevel.LOW_RISK_WRITE,
    ("gmail", "create_draft"): RiskLevel.LOW_RISK_WRITE,

    # Read-Only
    ("crm", "search"): RiskLevel.READ_ONLY,
    ("crm", "read"): RiskLevel.READ_ONLY,
    ("gmail", "search"): RiskLevel.READ_ONLY,
    ("gmail", "read"): RiskLevel.READ_ONLY,
    ("slack", "search"): RiskLevel.READ_ONLY,
    ("slack", "read"): RiskLevel.READ_ONLY,
    ("github", "search"): RiskLevel.READ_ONLY,
    ("github", "read"): RiskLevel.READ_ONLY,
    ("jira", "search"): RiskLevel.READ_ONLY,
    ("jira", "read"): RiskLevel.READ_ONLY,
    ("calendar", "search"): RiskLevel.READ_ONLY,
    ("calendar", "read"): RiskLevel.READ_ONLY,
}


class SafetyGate:
    """
    Deterministic Safety Gate ensuring no high-risk or customer-facing action
    can execute without explicit, cryptographically tokenized human authorization.
    """

    def __init__(self):
        self._pending_approvals: Dict[str, ApprovalRequest] = {}

    def classify_risk(self, app: str, operation: str) -> RiskLevel:
        """Deterministic policy lookup; never trusts LLM self-reporting for risk level."""
        key = (app.lower(), operation.lower())
        return DETERMINISTIC_RISK_POLICY.get(key, RiskLevel.HIGH_RISK_WRITE)

    def evaluate_action(self, action: ActionProposal) -> Tuple[bool, Optional[ApprovalRequest]]:
        """
        Evaluates an ActionProposal against deterministic safety policy.
        Returns:
            (can_execute_immediately, optional_approval_request)
        """
        # Override action risk_level with deterministic ground truth
        enforced_risk = self.classify_risk(action.app, action.action)
        action.risk_level = enforced_risk.value  # type: ignore

        if enforced_risk == RiskLevel.HIGH_RISK_WRITE:
            action.requires_approval = True
            token = f"nonce_{uuid.uuid4().hex[:10]}"
            req = ApprovalRequest(
                request_id=f"appr_{uuid.uuid4().hex[:8]}",
                action_id=action.action_id,
                summary=f"Authorization required: {action.app.upper()} {action.action} for {action.arguments.get('to', action.arguments.get('attendees', 'external'))}",
                proposed_action=action,
                risk_assessment=f"HIGH RISK: This action interacts externally with the customer. Rationale: {action.rationale}",
                approval_token=token,
                status="PENDING"
            )
            self._pending_approvals[token] = req
            logger.info(f"[SAFETY GATE] Action '{action.action_id}' requires human approval (Token: {token}).")
            return False, req

        # Low-risk write or read-only
        action.requires_approval = False
        return True, None

    def get_pending_requests(self) -> List[ApprovalRequest]:
        """Returns all approval requests currently in PENDING status."""
        return [r for r in self._pending_approvals.values() if r.status == "PENDING"]

    def resolve_approval(self, token: str, decision: Literal["APPROVE", "REJECT"], reason: str = "") -> Optional[ApprovalRequest]:
        req = self._pending_approvals.get(token)
        if not req:
            logger.warning(f"[SAFETY GATE] Invalid or expired approval token: {token}")
            return None

        if decision == "APPROVE":
            req.status = "APPROVED"
            logger.info(f"[SAFETY GATE] Approval granted for action '{req.action_id}'.")
        else:
            req.status = "REJECTED"
            logger.info(f"[SAFETY GATE] Approval REJECTED for action '{req.action_id}'. Reason: {reason or 'User denied'}")

        del self._pending_approvals[token]
        return req

    def get_pending(self) -> Dict[str, ApprovalRequest]:
        return dict(self._pending_approvals)


# Default singleton instance
safety_gate = SafetyGate()
