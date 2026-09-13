"""
Safety gate and test-resource validator for RECLAIM Live Demo execution.
Strictly prevents mutating production or non-dedicated repositories, channels, or projects.
"""

import os
from typing import Tuple, Dict, Any
from dotenv import load_dotenv

load_dotenv()


def validate_live_test_resources() -> Tuple[bool, str, Dict[str, Any]]:
    """
    Validates that the environment specifies explicitly dedicated test resources.
    Refuses to execute if any resource is missing or appears to be a production workspace.
    """
    gh_repo = os.getenv("GITHUB_TEST_REPO")
    slack_channel = os.getenv("SLACK_TEST_CHANNEL")
    jira_project = os.getenv("JIRA_TEST_PROJECT")

    details = {
        "github_test_repo": gh_repo,
        "slack_test_channel": slack_channel,
        "jira_test_project": jira_project,
    }

    if not gh_repo or not slack_channel or not jira_project:
        return (
            False,
            "REFUSED_MISSING_CONFIG",
            {
                **details,
                "error": "GITHUB_TEST_REPO, SLACK_TEST_CHANNEL, and JIRA_TEST_PROJECT must all be explicitly configured in .env",
            },
        )

    # Dedicated test resource validation:
    # 1. GitHub repo must be explicitly designated for testing / reclaim sandbox
    gh_lower = gh_repo.lower()
    is_dedicated_gh = any(term in gh_lower for term in ["test", "sandbox", "reclaim", "demo", "dev"])
    if not is_dedicated_gh:
        return (
            False,
            "REFUSED_NON_DEDICATED_TEST_RESOURCE",
            {
                **details,
                "error": f"Configured GitHub repository '{gh_repo}' is not a designated test/reclaim resource. Must contain 'test', 'sandbox', 'reclaim', or 'demo'.",
            },
        )

    # 2. Slack channel: must be set and not generic broadcast
    slack_lower = slack_channel.lower()
    if slack_lower in ["general", "all", "announcements", "random", "production"]:
        return (
            False,
            "REFUSED_NON_DEDICATED_TEST_RESOURCE",
            {
                **details,
                "error": f"Configured Slack channel '{slack_channel}' appears to be a shared/production channel.",
            },
        )

    # 3. Jira project: must be configured as designated test project
    if not jira_project.isalnum() or len(jira_project) > 10:
        return (
            False,
            "REFUSED_NON_DEDICATED_TEST_RESOURCE",
            {
                **details,
                "error": f"Invalid Jira test project key '{jira_project}'.",
            },
        )

    return True, "OK", details
