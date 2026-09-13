import enum
import logging
from typing import Set, Dict

logger = logging.getLogger(__name__)


class MissionState(str, enum.Enum):
    GOAL_RECEIVED = "GOAL_RECEIVED"
    PLANNING = "PLANNING"
    INVESTIGATING = "INVESTIGATING"
    EVIDENCE_SYNTHESIS = "EVIDENCE_SYNTHESIS"
    DECISION = "DECISION"
    WAITING_FOR_APPROVAL = "WAITING_FOR_APPROVAL"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    RECOVERING = "RECOVERING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class InvalidStateTransitionError(Exception):
    """Raised when an illegal transition is attempted on the agent state machine."""
    def __init__(self, from_state: MissionState, to_state: MissionState, reason: str = ""):
        self.from_state = from_state
        self.to_state = to_state
        self.reason = reason
        super().__init__(f"Illegal state transition from {from_state.value} to {to_state.value}. {reason}".strip())


class StateMachineController:
    """
    Deterministic Finite-State Controller for RECLAIM missions.
    Validates legal state transitions and enforces mission lifecycle invariants.
    """

    # Strict legal state transition graph
    LEGAL_TRANSITIONS: Dict[MissionState, Set[MissionState]] = {
        MissionState.GOAL_RECEIVED: {
            MissionState.PLANNING,
            MissionState.FAILED
        },
        MissionState.PLANNING: {
            MissionState.INVESTIGATING,
            MissionState.FAILED
        },
        MissionState.INVESTIGATING: {
            MissionState.EVIDENCE_SYNTHESIS,
            MissionState.RECOVERING,
            MissionState.FAILED
        },
        MissionState.EVIDENCE_SYNTHESIS: {
            MissionState.DECISION,
            MissionState.INVESTIGATING,  # Additional search cycle if confidence is insufficient
            MissionState.RECOVERING,
            MissionState.FAILED
        },
        MissionState.DECISION: {
            MissionState.WAITING_FOR_APPROVAL,
            MissionState.EXECUTING,
            MissionState.COMPLETED,  # When all actions verified
            MissionState.RECOVERING,
            MissionState.FAILED
        },
        MissionState.WAITING_FOR_APPROVAL: {
            MissionState.EXECUTING,   # Approved
            MissionState.RECOVERING,  # Rejected / Replanning
            MissionState.FAILED       # Aborted / Timed out
        },
        MissionState.EXECUTING: {
            MissionState.VERIFYING,
            MissionState.RECOVERING,
            MissionState.FAILED
        },
        MissionState.VERIFYING: {
            MissionState.DECISION,    # Action verified; proceed to next action or completion
            MissionState.RECOVERING,  # Verification failed; trigger recovery
            MissionState.FAILED
        },
        MissionState.RECOVERING: {
            MissionState.PLANNING,
            MissionState.INVESTIGATING,
            MissionState.DECISION,
            MissionState.FAILED
        },
        MissionState.COMPLETED: set(),  # Terminal state
        MissionState.FAILED: set()      # Terminal state
    }

    def __init__(self, initial_state: MissionState = MissionState.GOAL_RECEIVED):
        self._current_state = initial_state
        self._transition_history = [(initial_state, "Initialized")]

    @property
    def current_state(self) -> MissionState:
        return self._current_state

    @property
    def is_terminal(self) -> bool:
        return self._current_state in (MissionState.COMPLETED, MissionState.FAILED)

    def transition_to(self, new_state: MissionState, reason: str = "") -> None:
        """
        Transitions the agent to a new state if legal, otherwise raises InvalidStateTransitionError.
        """
        allowed = self.LEGAL_TRANSITIONS.get(self._current_state, set())
        if new_state not in allowed:
            raise InvalidStateTransitionError(
                from_state=self._current_state,
                to_state=new_state,
                reason=f"Current state {self._current_state.value} only allows transitions to: {[s.value for s in allowed]}"
            )

        prev_state = self._current_state
        self._current_state = new_state
        self._transition_history.append((new_state, reason))
        logger.info(f"[STATE MACHINE] {prev_state.value} -> {new_state.value} (Reason: {reason or 'N/A'})")

    def get_history(self):
        return list(self._transition_history)
