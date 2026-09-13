from typing import List, Dict, Any, Optional, Literal, Union
from pydantic import BaseModel, Field


class ProbeSchema(BaseModel):
    """Minimal schema used for fast, zero-cost provider health probes."""
    status: Literal["ok", "ready"] = "ok"
    provider_name: str = Field(description="Name of the responding provider")
    latency_probe_id: str = Field(description="Deterministic probe identifier")


class StepQueryParams(BaseModel):
    """Structured query parameters for investigation steps."""
    model_config = {"extra": "allow"}
    query: Optional[str] = Field(default=None, description="Search query string")
    channel: Optional[str] = Field(default=None, description="Slack channel name")
    issue_key: Optional[str] = Field(default=None, description="Jira issue key")
    limit: Optional[int] = Field(default=None, description="Maximum items to return")

    def get(self, key: str, default: Any = None) -> Any:
        val = getattr(self, key, None)
        if val is not None:
            return val
        if hasattr(self, "__pydantic_extra__") and self.__pydantic_extra__:
            return self.__pydantic_extra__.get(key, default)
        return default

    def __getitem__(self, item: str) -> Any:
        val = self.get(item)
        if val is None:
            raise KeyError(item)
        return val

    def __contains__(self, item: str) -> bool:
        return self.get(item) is not None

    def keys(self):
        d = self.model_dump(exclude_none=True)
        return d.keys()

    def items(self):
        d = self.model_dump(exclude_none=True)
        return d.items()

    def values(self):
        d = self.model_dump(exclude_none=True)
        return d.values()


class InvestigationStep(BaseModel):
    """An autonomous investigation step planned across an external application."""
    step_id: str = Field(description="Unique step identifier, e.g., 'inv_crm_01'")
    app: Literal["crm", "gmail", "slack", "jira", "github", "calendar"] = Field(
        description="Target external application to query"
    )
    intent: str = Field(description="Goal of this investigation query")
    query_params: StepQueryParams = Field(
        default_factory=StepQueryParams,
        description="Query parameters passed to the tool adapter"
    )
    expected_information: str = Field(
        description="What specific data or indicator this step seeks to unearth"
    )


class AgentPlan(BaseModel):
    """The structured investigative plan decomposed from the high-level business goal."""
    goal: str = Field(description="The operational objective, e.g. 'Save Acme Corp.'")
    initial_assessment: str = Field(description="High-level understanding of the crisis")
    investigation_steps: List[InvestigationStep] = Field(
        description="Sequence of cross-app search queries to discover facts"
    )
    risk_summary: str = Field(description="Potential risks during investigation")


class EvidenceClaim(BaseModel):
    """A factual claim extracted from an external application."""
    claim_id: str = Field(description="Unique identifier for this piece of evidence, e.g., 'ev_gmail_01'")
    source_app: Literal["crm", "gmail", "slack", "jira", "github", "calendar"] = Field(
        description="Application where evidence was found"
    )
    fact: str = Field(description="Objective statement of observed reality")
    supporting_reference: str = Field(
        description="Reference ID or link (e.g. 'Email from sarah@acme.com', 'PROD-1042', 'PR #882')"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence score between 0.0 and 1.0"
    )


class RootCauseHypothesis(BaseModel):
    """A synthesized root cause explaining why the account is at risk."""
    hypothesis_id: str = Field(description="Hypothesis identifier, e.g., 'hyp_01'")
    title: str = Field(description="Short summary of the root cause")
    description: str = Field(description="Detailed technical and business explanation")
    culprit_app: Literal["crm", "gmail", "slack", "jira", "github", "calendar", "multiple"] = Field(
        description="Primary subsystem where breakdown originated"
    )
    corroborating_claim_ids: List[str] = Field(
        min_length=1,
        description="List of claim_ids from EvidenceClaim that corroborate this hypothesis"
    )
    confidence: float = Field(
        ge=0.0, le=1.0,
        description="Overall confidence in this root cause"
    )


class ActionArguments(BaseModel):
    """Structured action arguments strictly conforming to Groq structured schema."""
    model_config = {"extra": "allow"}
    key: Optional[str] = Field(default=None, description="Issue or resource key, e.g. 'SCRUM-5'")
    issue_id: Optional[str] = Field(default=None, description="Issue or ticket ID")
    ticket_id: Optional[str] = Field(default=None, description="Alternative ticket identifier")
    summary: Optional[str] = Field(default=None, description="Issue summary or title")
    comment: Optional[str] = Field(default=None, description="Comment or escalation note")
    priority: Optional[str] = Field(default=None, description="Target priority, e.g. 'High' or 'Highest'")
    channel: Optional[str] = Field(default=None, description="Slack channel name or ID, e.g. '#incident-war-room'")
    text: Optional[str] = Field(default=None, description="Message text")
    message: Optional[str] = Field(default=None, description="Alternative message text")
    to: Optional[str] = Field(default=None, description="Recipient email address")
    recipient: Optional[str] = Field(default=None, description="Recipient identifier")
    subject: Optional[str] = Field(default=None, description="Email subject")
    body: Optional[str] = Field(default=None, description="Email or document body")
    attendees: Optional[Union[str, List[str]]] = Field(default=None, description="Meeting attendees list")

    def get(self, key: str, default: Any = None) -> Any:
        val = getattr(self, key, None)
        if val is not None:
            return val
        if hasattr(self, "__pydantic_extra__") and self.__pydantic_extra__:
            return self.__pydantic_extra__.get(key, default)
        return default

    def __getitem__(self, item: str) -> Any:
        val = self.get(item)
        if val is None:
            raise KeyError(item)
        return val

    def __contains__(self, item: str) -> bool:
        return self.get(item) is not None

    def keys(self):
        d = self.model_dump(exclude_none=True)
        return d.keys()

    def items(self):
        d = self.model_dump(exclude_none=True)
        return d.items()

    def values(self):
        d = self.model_dump(exclude_none=True)
        return d.values()


class ActionExpectedEffect(BaseModel):
    """Expected post-state for independent verification engine."""
    model_config = {"extra": "allow"}
    status: Optional[str] = Field(default=None, description="Expected status field, e.g. 'Escalated' or 'Sent'")
    description: str = Field(default="State updated successfully", description="Expected post-state description")

    def get(self, key: str, default: Any = None) -> Any:
        val = getattr(self, key, None)
        if val is not None:
            return val
        if hasattr(self, "__pydantic_extra__") and self.__pydantic_extra__:
            return self.__pydantic_extra__.get(key, default)
        return default

    def keys(self):
        d = self.model_dump(exclude_none=True)
        return d.keys()

    def items(self):
        d = self.model_dump(exclude_none=True)
        return d.items()

    def values(self):
        d = self.model_dump(exclude_none=True)
        return d.values()


class ActionVerificationMethod(BaseModel):
    """Verification methodology specification."""
    model_config = {"extra": "allow"}
    type: str = Field(default="read_check", description="Verification method type, e.g. 'read_check'")
    check: str = Field(default="status_matches", description="Specific verification check, e.g. 'status_matches'")

    def get(self, key: str, default: Any = None) -> Any:
        val = getattr(self, key, None)
        if val is not None:
            return val
        if hasattr(self, "__pydantic_extra__") and self.__pydantic_extra__:
            return self.__pydantic_extra__.get(key, default)
        return default

    def keys(self):
        d = self.model_dump(exclude_none=True)
        return d.keys()

    def items(self):
        d = self.model_dump(exclude_none=True)
        return d.items()

    def values(self):
        d = self.model_dump(exclude_none=True)
        return d.values()


class ActionProposal(BaseModel):
    """A proposed external action to remediate the crisis."""
    action_id: str = Field(description="Unique action proposal ID, e.g., 'act_jira_p0'")
    app: Literal["crm", "gmail", "slack", "jira", "github", "calendar"] = Field(
        description="Destination application for the action"
    )
    action: str = Field(description="Action name, e.g. 'update_issue', 'send_email', 'create_meeting'")
    arguments: ActionArguments = Field(
        default_factory=ActionArguments,
        description="Arguments for the tool adapter"
    )
    risk_level: Literal["READ_ONLY", "LOW_RISK_WRITE", "HIGH_RISK_WRITE"] = Field(
        description="Safety tier: READ_ONLY, LOW_RISK_WRITE (internal/safe), HIGH_RISK_WRITE (customer-facing)"
    )
    requires_approval: bool = Field(
        description="Must be True if risk_level is HIGH_RISK_WRITE"
    )
    rationale: str = Field(description="Why this specific action is justified by the evidence")
    expected_effect: ActionExpectedEffect = Field(
        default_factory=ActionExpectedEffect,
        description="Expected post-state to be verified by the Verification Engine"
    )
    verification_method: ActionVerificationMethod = Field(
        default_factory=ActionVerificationMethod,
        description="Specification for how the Verification Engine will confirm effect"
    )
    is_optional: bool = Field(
        default=False,
        description="True if action is optional and non-blocking for mission completion"
    )


class ApprovalRequest(BaseModel):
    """A human-in-the-loop authorization request for a high-risk action."""
    request_id: str = Field(description="Unique approval request ID")
    action_id: str = Field(description="Action ID awaiting approval")
    summary: str = Field(description="Clear explanation of proposed action and why it matters")
    proposed_action: ActionProposal = Field(description="The complete proposed action payload")
    risk_assessment: str = Field(description="Consequences if executed or if refused")
    approval_token: str = Field(description="One-time nonce required to authorize execution")
    status: Literal["PENDING", "APPROVED", "REJECTED"] = Field(default="PENDING")


class VerificationResult(BaseModel):
    """Result from independent verification query after action execution."""
    action_id: str = Field(description="The action being verified")
    verified: bool = Field(description="True if post-state matches expected effect")
    verification_method: str = Field(description="Name or type of verification performed")
    observed_state: Dict[str, Any] = Field(description="The actual state observed in the target app")
    discrepancy: Optional[str] = Field(default=None, description="Explanation of any mismatch")
    details: str = Field(description="Audit summary of verification check")


class MissionDecision(BaseModel):
    """The synthesized decision package containing root cause and planned remediation."""
    decision_id: str = Field(description="Decision identifier, e.g., 'dec_acme_rescue'")
    reasoning_status: Literal["LLM_REASONING", "DEGRADED_REASONING", "DETERMINISTIC_CONTROL", "FAILED"] = Field(
        default="LLM_REASONING",
        description="Integrity status of the reasoning producing this decision"
    )
    decision_source: str = Field(
        default="groq:openai/gpt-oss-120b",
        description="Source provenance, e.g. groq:openai/gpt-oss-120b or deterministic_control"
    )
    model: str = Field(
        default="openai/gpt-oss-120b",
        description="Exact model ID used for decision synthesis"
    )
    request_id: str = Field(
        default="",
        description="Provider request ID for auditable trace linking"
    )
    fallback_used: bool = Field(
        default=False,
        description="True if fallback model was invoked"
    )
    root_cause_summary: str = Field(description="Clear diagnosis of the customer churn danger")
    hypotheses: List[RootCauseHypothesis] = Field(description="Root cause hypotheses evaluated")
    selected_actions: List[ActionProposal] = Field(description="Remediation actions ordered by priority")
    remediation_strategy: str = Field(description="Overall strategic explanation")
    estimated_risk: str = Field(description="Residual risk assessment")


class GoalInterpretation(BaseModel):
    """Structured decomposition of a high-level operational user objective."""
    target_entity: str = Field(description="The principal customer, account, or subsystem at risk")
    desired_outcome: str = Field(description="Core business impact to achieve")
    success_conditions: List[str] = Field(description="Verifiable conditions marking mission success")
    constraints: List[str] = Field(description="Operational boundaries and safety limits")
    initial_information_gaps: List[str] = Field(description="Unknowns that must be resolved via multi-app search")
    candidate_app_domains: List[str] = Field(description="Recommended external applications to inspect")


class EvidenceSynthesisResult(BaseModel):
    """Synthesized diagnosis of collected multi-app facts."""
    root_cause_hypotheses: List[RootCauseHypothesis] = Field(description="Hypotheses evaluated from facts")
    supporting_evidence_ids: List[str] = Field(description="IDs of corroborating evidence items")
    contradicting_evidence_ids: List[str] = Field(default_factory=list, description="IDs of conflicting or red-herring evidence")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence in primary hypothesis")
    business_impact: str = Field(description="Quantified or qualified impact on the customer")
    information_gaps: List[str] = Field(default_factory=list, description="Unresolved questions")
    recommended_next_investigation: Optional[str] = Field(default=None, description="Next query if confidence is low")

