import re
import logging
from typing import Optional
from reclaim.llm.base import LLMProvider
from reclaim.llm.schemas import GoalInterpretation

logger = logging.getLogger(__name__)


class GoalParser:
    """
    Interprets high-level user commands into structured mission parameters.
    Works for general business rescue objectives (e.g. 'Save Acme Corp.', 'Protect the Acme renewal').
    """

    def __init__(self, llm_provider: Optional[LLMProvider] = None):
        self.llm_provider = llm_provider

    async def parse_goal(self, goal_text: str) -> GoalInterpretation:
        """
        Parses goal into a structured GoalInterpretation schema.
        Uses LLM structured generation when available, falling back to deterministic extraction.
        """
        if self.llm_provider:
            prompt = (
                f"Analyze the following operational business rescue objective:\n"
                f"\"{goal_text}\"\n\n"
                "Decompose this into: target_entity, desired_outcome, success_conditions, constraints, "
                "initial_information_gaps, and candidate_app_domains (e.g. crm, gmail, slack, jira, github, calendar)."
            )
            try:
                resp = await self.llm_provider.generate_structured(
                    schema=GoalInterpretation,
                    prompt=prompt
                )
                return resp.parsed
            except Exception as e:
                logger.warning(f"LLM goal parsing encountered issue ({e}); using deterministic parser.")

        # Deterministic extraction fallback
        return self._deterministic_parse(goal_text)

    def _deterministic_parse(self, goal_text: str) -> GoalInterpretation:
        clean = goal_text.strip()
        # Heuristic entity extraction: match keywords like save, protect, recover, investigate, resolve, why is
        match = re.search(
            r"(?:save|protect|recover|investigate|resolve|why is)\s+(?:the\s+)?([A-Za-z0-9\s\-]+?)(?:'s|\s+account|\s+renewal|\s+churn|\s+production|\s+crisis|\s+api|\s+integration|\.|$)",
            clean,
            re.IGNORECASE
        )
        if match:
            target = match.group(1).strip()
            # Clean common trailing stop words
            target = re.sub(r"\b(production|crisis|integration|api|renewal|account)\b", "", target, flags=re.IGNORECASE).strip()
        else:
            words = [w for w in clean.replace(".", "").split() if w.lower() not in ("resolve", "the", "crisis", "issue", "failure")]
            target = words[0] if words else "Target Account"

        if not target:
            target = "Acme Corp"

        # Normalize known company forms
        if target.lower() in ("acme", "acme corp", "acme corporation"):
            canonical_target = "Acme Corp"
        elif target.lower() in ("beta", "beta corp", "beta corporation"):
            canonical_target = "Beta Corp"
        else:
            canonical_target = target

        return GoalInterpretation(
            target_entity=canonical_target,
            desired_outcome=f"Identify and resolve technical and operational outage drivers for {canonical_target} to preserve business continuity.",
            success_conditions=[
                "Root cause identified with multi-app evidence",
                "Internal engineering ticket prioritized or updated",
                "Incident response coordinated across Slack and Jira",
                "High-risk customer actions approved before execution",
                "All state mutations independently verified"
            ],
            constraints=[
                "Never execute high-risk customer actions without human authorization",
                "Max 3 investigation search cycles",
                "No unverified writes allowed in final mission status"
            ],
            initial_information_gaps=[
                f"What technical failure is impacting {canonical_target}?",
                "What recent code changes, PRs, or deployments correlate with the failure?",
                "Are engineering teams already aware or is an active ticket tracked?",
                "What is the operational impact on customer workflows?"
            ],
            candidate_app_domains=["slack", "jira", "github", "crm", "gmail", "calendar"]
        )
