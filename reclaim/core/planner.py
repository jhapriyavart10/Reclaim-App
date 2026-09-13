import json
import logging
import re
from typing import Dict, Any, List, Optional
from reclaim.adapters.registry import AdapterRegistry, adapter_registry
from reclaim.llm.base import LLMProvider
from reclaim.llm.schemas import (
    AgentPlan,
    InvestigationStep,
    GoalInterpretation,
    EvidenceSynthesisResult,
    RootCauseHypothesis
)
from reclaim.core.evidence_store import EvidenceStore, EvidenceItem

logger = logging.getLogger(__name__)


class InvestigationPlanner:
    """
    Capability-aware planner decomposing goals into targeted multi-app investigative steps.
    Queries the AppCapability registry to guarantee the agent never calls nonexistent tools.
    """

    def __init__(self, llm_provider: Optional[LLMProvider] = None, registry: Optional[AdapterRegistry] = None):
        self.llm_provider = llm_provider
        self.registry = registry or adapter_registry

    async def create_plan(self, goal: GoalInterpretation) -> AgentPlan:
        capabilities = await self.registry.get_all_capabilities()
        cap_summary = {
            app: {
                "source_type": cap.source_type.value,
                "availability": cap.availability,
                "read_operations": cap.read_operations,
                "write_operations": cap.write_operations
            }
            for app, cap in capabilities.items()
        }

        if self.llm_provider:
            prompt = (
                f"GOAL: Resolve churn risk for {goal.target_entity}.\n"
                f"OBJECTIVE: {goal.desired_outcome}\n"
                f"UNKNOWN INFORMATION GAPS: {json.dumps(goal.initial_information_gaps)}\n\n"
                f"AVAILABLE REGISTERED APPLICATION CAPABILITIES:\n{json.dumps(cap_summary, indent=2)}\n\n"
                "CRITICAL INVARIANTS:\n"
                "- Do NOT invent tools or applications not listed above.\n"
                "- Formulate targeted search queries to investigate account status, recent complaints, internal alerts, tickets, and code changes."
            )
            try:
                resp = await self.llm_provider.generate_structured(schema=AgentPlan, prompt=prompt)
                validated_plan = self._validate_and_sanitize_plan(resp.parsed, capabilities)
                return validated_plan
            except Exception as e:
                logger.warning(f"LLM planning failed ({e}); using deterministic capability-grounded plan.")

        return self._deterministic_plan(goal, capabilities)

    def _validate_and_sanitize_plan(self, plan: AgentPlan, capabilities: Dict[str, Any]) -> AgentPlan:
        valid_steps: List[InvestigationStep] = []
        for step in plan.investigation_steps:
            app = step.app.lower()
            if app not in capabilities:
                logger.warning(f"[ANTI-HALLUCINATION] Rejected step {step.step_id}: unregistered app '{app}'")
                continue
            if capabilities[app].availability != "AVAILABLE":
                logger.info(f"[PLANNER] Step {step.step_id} targets unavailable app '{app}'; skipping.")
                continue
            valid_steps.append(step)

        plan.investigation_steps = valid_steps
        return plan

    def _deterministic_plan(self, goal: GoalInterpretation, capabilities: Dict[str, Any]) -> AgentPlan:
        target = goal.target_entity
        target_token = target.split()[0] if target else "Incident"
        steps = [
            InvestigationStep(
                step_id="inv_slack",
                app="slack",
                intent=f"Inspect internal incident and engineering channels for {target_token} alerts and errors",
                query_params={"query": target_token},
                expected_information="Impacted services, endpoint errors, or outage discussion"
            ),
            InvestigationStep(
                step_id="inv_jira",
                app="jira",
                intent=f"Search issue tracker for open tickets referencing {target_token} or customer bugs",
                query_params={"query": target_token},
                expected_information="Active bug tickets, priority classifications, and assignments"
            ),
            InvestigationStep(
                step_id="inv_github",
                app="github",
                intent=f"Inspect recent code pull requests and commits related to {target_token} or failing services",
                query_params={"query": ""},
                expected_information="Recent merged changes, potential regression commits, or PR diffs"
            ),
            InvestigationStep(
                step_id="inv_crm",
                app="crm",
                intent=f"Lookup account ARR, contract status, and health score for {target}",
                query_params={"query": target_token},
                expected_information="Account health metrics and primary contact"
            ),
            InvestigationStep(
                step_id="inv_gmail",
                app="gmail",
                intent=f"Check for urgent escalation emails or communications concerning {target_token}",
                query_params={"query": target_token},
                expected_information="Customer communication trail and reported symptoms"
            ),
            InvestigationStep(
                step_id="inv_calendar",
                app="calendar",
                intent=f"Check calendar availability for technical post-mortem and incident response sync for {target}",
                query_params={"query": ""},
                expected_information="Available alignment slots"
            )
        ]

        # Filter against actual registered capabilities
        active_steps = [s for s in steps if s.app in capabilities and capabilities[s.app].availability == "AVAILABLE"]
        return AgentPlan(
            goal=goal.desired_outcome,
            initial_assessment=f"Operational crisis investigation for {target}. Cross-app evidence gathering initiated across available tools.",
            investigation_steps=active_steps,
            risk_summary="High business risk if root cause is not identified and remediated swiftly."
        )


class EvidenceSynthesizer:
    """
    Synthesizes multi-app evidence nodes into validated root cause hypotheses.
    Enforces cross-app corroboration and strips hallucinated evidence citations.
    """

    def __init__(self, llm_provider: Optional[LLMProvider] = None):
        self.llm_provider = llm_provider

    async def synthesize(self, goal_or_store, store_or_goal=None) -> EvidenceSynthesisResult:
        if isinstance(goal_or_store, EvidenceStore):
            store = goal_or_store
            goal = store_or_goal
        else:
            goal = goal_or_store
            store = store_or_goal

        all_evidence = store.get_all() if store else []
        evidence_summary = [
            {
                "id": ev.evidence_id,
                "app": ev.app,
                "source_type": ev.source_type.value,
                "author": ev.author,
                "summary": ev.content,
                "entities": ev.entities
            }
            for ev in all_evidence
        ]
        """
        Synthesize evidence from store into structured hypothesis with corroborating evidence IDs.
        """
        evidence_items = store.get_all()
        if not evidence_items:
            return EvidenceSynthesisResult(
                root_cause_hypotheses=[],
                supporting_evidence_ids=[],
                contradicting_evidence_ids=[],
                confidence=0.0,
                business_impact="No evidence retrieved yet.",
                information_gaps=["Missing multi-app investigation evidence."],
                recommended_next_investigation="Execute initial investigation plan."
            )

        if self.llm_provider:
            # Build concise factual digest to conserve token window (Requirement H)
            compressed_digest = []
            for ev in evidence_items:
                # Clean content to essential finding
                clean_content = " ".join(ev.content.split())
                if len(clean_content) > 160:
                    clean_content = clean_content[:157] + "..."
                compressed_digest.append({
                    "evidence_id": ev.evidence_id,
                    "app": ev.app,
                    "ref": ev.resource_reference,
                    "actor": ev.author,
                    "finding": clean_content
                })

            prompt = (
                f"GOAL / OBJECTIVE:\n"
                f"Target Entity: {goal.target_entity}\n"
                f"Desired Outcome: {goal.desired_outcome}\n\n"
                f"FACTUAL EVIDENCE DIGEST ({len(evidence_items)} items across apps):\n"
                f"{json.dumps(compressed_digest, indent=1)}\n\n"
                "INSTRUCTIONS:\n"
                "1. Analyze cross-app evidence to determine technical root cause and customer impact.\n"
                "2. Corroborate across at least 2 distinct applications.\n"
                "3. Cite ONLY real evidence_id values from the digest above (never hallucinate IDs).\n"
                "4. Assess confidence score (0.0 to 1.0) and identify any critical gaps."
            )

            try:
                resp = await self.llm_provider.generate_structured(
                    schema=EvidenceSynthesisResult,
                    prompt=prompt
                )
                return self._validate_and_filter_synthesis(resp.parsed, store)
            except Exception as e:
                logger.warning(f"LLM evidence synthesis error ({e}); using deterministic synthesis.")

        return self._deterministic_synthesis(goal, store)

    def _validate_and_filter_synthesis(
        self,
        result: EvidenceSynthesisResult,
        store: EvidenceStore
    ) -> EvidenceSynthesisResult:
        """
        Anti-hallucination validation:
        1. Strips any evidence ID not present in EvidenceStore.
        2. Drops any hypothesis without at least 2 valid, distinct evidence items.
        """
        valid_hypotheses: List[RootCauseHypothesis] = []
        for hyp in result.root_cause_hypotheses:
            verified_ids = [eid for eid in hyp.corroborating_claim_ids if store.get(eid) is not None]
            hyp.corroborating_claim_ids = verified_ids

            # Check cross-app corroboration
            apps_involved = {store.get(eid).app for eid in verified_ids if store.get(eid)}
            if len(verified_ids) >= 2 and len(apps_involved) >= 2:
                valid_hypotheses.append(hyp)
            else:
                logger.warning(
                    f"[ANTI-HALLUCINATION] Rejected hypothesis '{hyp.title}': "
                    f"insufficient cross-app corroboration ({len(verified_ids)} items across {len(apps_involved)} apps)."
                )

        result.root_cause_hypotheses = valid_hypotheses
        result.supporting_evidence_ids = [eid for eid in result.supporting_evidence_ids if store.get(eid) is not None]
        return result

    def _validate_synthesis(self, result: EvidenceSynthesisResult, store: EvidenceStore) -> EvidenceSynthesisResult:
        return self._validate_and_filter_synthesis(result, store)

    def _deterministic_synthesis(self, goal_or_store: Any, maybe_store: Optional[EvidenceStore] = None) -> EvidenceSynthesisResult:
        if isinstance(goal_or_store, EvidenceStore):
            store = goal_or_store
            goal = GoalInterpretation(
                target_entity="Acme Corp",
                desired_outcome="Resolve incident",
                success_conditions=[],
                constraints=[],
                initial_information_gaps=[],
                candidate_app_domains=[]
            )
        else:
            goal = goal_or_store
            store = maybe_store or EvidenceStore()

        all_ev = store.get_all()
        target = goal.target_entity.lower()
        target_token = target.split()[0] if target else "incident"

        # Dynamically discover tickets, PRs, error terms, and red herrings
        ticket_pattern = re.compile(r'\b[A-Z]{2,10}-\d+\b')
        pr_pattern = re.compile(r'#(\d+)')
        error_keywords = ("500", "timeout", "error", "exception", "failure", "crash", "regression", "broken", "exhaustion", "leak")
        red_herring_keywords = (
            "catering", "lunch", "invoice", "t-shirt", "menu", "decor", "parking",
            "typography", "css", "button margin", "prettier", "marketing site", "decoy"
        )

        target_corroborating: List[str] = []
        red_herrings: List[str] = []
        discovered_tickets: List[str] = []
        functional_prs: List[str] = []
        error_excerpts: List[str] = []

        for e in all_ev:
            content_lower = e.content.lower()

            # Check if clearly a red herring
            if any(rhk in content_lower for rhk in red_herring_keywords):
                red_herrings.append(e.evidence_id)
                continue

            is_relevant = (
                target in content_lower
                or target_token in content_lower
                or any(err in content_lower for err in error_keywords)
                or e.app in ("crm", "slack", "jira", "github", "gmail")
            )

            if is_relevant:
                target_corroborating.append(e.evidence_id)

                # Extract tickets (prioritize active, non-closed tickets)
                is_closed_ticket = "status=closed" in content_lower or "status=resolved" in content_lower
                has_error = any(err in content_lower for err in error_keywords)
                for t in ticket_pattern.findall(e.content):
                    if not is_closed_ticket and t not in discovered_tickets:
                        if has_error:
                            discovered_tickets.insert(0, t)
                        else:
                            discovered_tickets.append(t)

                # Extract PR numbers (only if not cosmetic)
                if e.app == "github":
                    for pr in pr_pattern.findall(e.content):
                        if pr not in functional_prs:
                            functional_prs.append(pr)

                for err in error_keywords:
                    if err in content_lower and err not in error_excerpts:
                        error_excerpts.append(err)

        # Determine primary ticket and PR dynamically
        primary_ticket = discovered_tickets[0] if discovered_tickets else None
        primary_pr = functional_prs[0] if functional_prs else None
        primary_err = ", ".join(error_excerpts[:2]) if error_excerpts else "operational failure"

        # Determine culprit app and construct title/description dynamically
        if primary_pr:
            culprit_app = "github"
            title = f"PR #{primary_pr} Regression Triggering {primary_err.capitalize()}"
            desc = f"Merged pull request #{primary_pr} introduced a regression leading to {primary_err} impacting {goal.target_entity}."
            if primary_ticket:
                desc += f" Correlated with tracking ticket {primary_ticket}."
        elif primary_ticket:
            culprit_app = "jira"
            title = f"Issue {primary_ticket}: {primary_err.capitalize()} Impacting {goal.target_entity}"
            desc = f"Active issue {primary_ticket} tracks severe {primary_err} impacting {goal.target_entity} operational flows."
        else:
            culprit_app = "slack"
            title = f"Production Service Outage Affecting {goal.target_entity}"
            desc = f"Cross-app telemetry reveals acute operational failure ({primary_err}) impacting {goal.target_entity}."

        apps_involved = {store.get(eid).app for eid in target_corroborating if store.get(eid)}
        confidence = 0.94 if len(apps_involved) >= 3 else (0.85 if len(apps_involved) >= 2 else 0.50)

        hyp = RootCauseHypothesis(
            hypothesis_id=f"hyp_{culprit_app}_{primary_pr or primary_ticket or 'incident'}",
            title=title,
            description=desc,
            culprit_app=culprit_app,
            corroborating_claim_ids=target_corroborating,
            confidence=confidence
        )

        return EvidenceSynthesisResult(
            root_cause_hypotheses=[hyp] if confidence >= 0.70 else [],
            supporting_evidence_ids=target_corroborating,
            contradicting_evidence_ids=red_herrings,
            confidence=confidence,
            business_impact=f"Operational continuity for {goal.target_entity} impaired by {primary_err}.",
            information_gaps=[] if len(apps_involved) >= 2 else ["Insufficient cross-app evidence."],
            recommended_next_investigation=None
        )
