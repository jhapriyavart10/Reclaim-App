import os
import logging
from typing import Dict, Literal, Optional, Any
from dotenv import load_dotenv

load_dotenv()

from reclaim.adapters.base import BaseAdapter, AppCapability, SourceType
from reclaim.adapters.live.github import LiveGitHubAdapter
from reclaim.adapters.live.slack import LiveSlackAdapter
from reclaim.adapters.live.jira import LiveJiraAdapter
from reclaim.adapters.simulated.github import SimulatedGitHubAdapter
from reclaim.adapters.simulated.slack import SimulatedSlackAdapter
from reclaim.adapters.simulated.jira import SimulatedJiraAdapter
from reclaim.adapters.simulated.gmail import SimulatedGmailAdapter
from reclaim.adapters.simulated.calendar import SimulatedCalendarAdapter
from reclaim.adapters.simulated.crm import SimulatedCRMAdapter

logger = logging.getLogger(__name__)

AppMode = Literal["live", "simulation", "hybrid"]


class AdapterRegistry:
    """
    Central registry managing application adapter resolution across LIVE, SIMULATION, and HYBRID modes.
    Ensures the core agent interacts strictly through normalized interfaces.
    """

    def __init__(self, mode: Optional[AppMode] = None, engine: Optional[Any] = None):
        policy = (os.getenv("MISSION_DATA_POLICY") or "").lower()
        if policy in ("strict_live", "live"):
            env_mode = "live"
        elif policy in ("simulation", "simulated"):
            env_mode = "simulation"
        else:
            env_mode = os.getenv("APP_MODE", "hybrid").lower()

        self.mode: AppMode = mode or (env_mode if env_mode in ["live", "simulation", "hybrid"] else "hybrid")  # type: ignore
        self.engine = engine

        # Instantiated adapters
        self.simulated_adapters: Dict[str, BaseAdapter] = {
            "github": SimulatedGitHubAdapter(engine=engine),
            "slack": SimulatedSlackAdapter(engine=engine),
            "jira": SimulatedJiraAdapter(engine=engine),
            "gmail": SimulatedGmailAdapter(engine=engine),
            "calendar": SimulatedCalendarAdapter(engine=engine),
            "crm": SimulatedCRMAdapter(engine=engine),
        }

        self.live_adapters: Dict[str, BaseAdapter] = {
            "github": LiveGitHubAdapter(),
            "slack": LiveSlackAdapter(),
            "jira": LiveJiraAdapter()
        }

    async def get_adapter(self, app_name: str) -> BaseAdapter:
        """
        Resolves the appropriate adapter based on the active APP_MODE and credential health.
        """
        app = app_name.lower()

        if self.mode == "simulation":
            if app in self.simulated_adapters:
                return self.simulated_adapters[app]
            raise ValueError(f"Unknown application: {app}")

        if self.mode == "live":
            if app in self.live_adapters:
                cap = await self.live_adapters[app].get_capabilities()
                if cap.availability == "AVAILABLE":
                    return self.live_adapters[app]
                raise RuntimeError(f"Live adapter for '{app}' requested but credentials are UNAVAILABLE.")
            raise ValueError(f"Live adapter for '{app}' is not registered under live mode.")

        # HYBRID Mode (Default)
        if app in self.live_adapters:
            cap = await self.live_adapters[app].get_capabilities()
            if cap.availability == "AVAILABLE":
                return self.live_adapters[app]
            # Graceful fallback to simulation
            logger.info(f"[HYBRID] Live adapter for '{app}' is unconfigured; using SIMULATED adapter.")
            return self.simulated_adapters[app]

        if app in self.simulated_adapters:
            return self.simulated_adapters[app]

        raise ValueError(f"Unknown application: {app}")

    async def get_all_capabilities(self) -> Dict[str, AppCapability]:
        """
        Returns dynamic capabilities for all registered applications under the current mode.
        """
        capabilities: Dict[str, AppCapability] = {}
        apps = ["github", "slack", "jira"] if self.mode == "live" else ["github", "slack", "jira", "gmail", "calendar", "crm"]
        for app in apps:
            adapter = await self.get_adapter(app)
            capabilities[app] = await adapter.get_capabilities()
        return capabilities

    discover_capabilities = get_all_capabilities


# Global default registry
adapter_registry = AdapterRegistry()
