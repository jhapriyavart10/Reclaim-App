"""
RECLAIM Live Integration Smoke Test CLI.
Performs safe, READ-ONLY inspection across the 3 primary live external integrations:
- GitHub
- Slack
- Jira

Never mutates external state.
Reports:
Status: AUTHENTICATION_REQUIRED / LIVE_VERIFIED
Endpoint, Operation, Resource, Latency, Result.
"""

import asyncio
import os
import sys
import time
from typing import Dict, Any
from dotenv import load_dotenv

load_dotenv()

from reclaim.adapters.live.github import LiveGitHubAdapter
from reclaim.adapters.live.slack import LiveSlackAdapter
from reclaim.adapters.live.jira import LiveJiraAdapter
from reclaim.adapters.base import ConnectorStatus


async def probe_live_adapters() -> list[Dict[str, Any]]:
    results = []

    # 1. GitHub
    gh_token = os.getenv("GITHUB_TOKEN")
    gh_test_repo = os.getenv("GITHUB_TEST_REPO") or os.getenv("GITHUB_REPO")
    gh_endpoint = "https://api.github.com"
    if gh_token and gh_test_repo:
        t0 = time.perf_counter()
        adapter = LiveGitHubAdapter(token=gh_token)
        try:
            resp = await adapter.search("is:pr", repo=gh_test_repo)
            lat = (time.perf_counter() - t0) * 1000.0
            found = resp.data.get("total_count", len(resp.data.get("items", []))) if resp.data else 0
            if resp.success:
                status = ConnectorStatus.LIVE_VERIFIED.value
                result = f"Confirmed {found} items in {gh_test_repo}"
            else:
                status = ConnectorStatus.UNAVAILABLE.value
                result = f"API Error: {resp.error}"
            results.append({
                "app": "GitHub",
                "status": status,
                "endpoint": gh_endpoint,
                "op": "search_prs",
                "resource": gh_test_repo,
                "latency": f"{lat:.1f}ms",
                "result": result
            })
        except Exception as e:
            results.append({
                "app": "GitHub",
                "status": ConnectorStatus.UNAVAILABLE.value,
                "endpoint": gh_endpoint,
                "op": "search_prs",
                "resource": gh_test_repo or "N/A",
                "latency": "N/A",
                "result": f"Connection Exception: {str(e)}"
            })
    else:
        results.append({
            "app": "GitHub",
            "status": ConnectorStatus.AUTHENTICATION_REQUIRED.value,
            "endpoint": gh_endpoint,
            "op": "search_prs",
            "resource": gh_test_repo or "UNCONFIGURED (Set GITHUB_TEST_REPO)",
            "latency": "N/A",
            "result": "SKIPPED - GITHUB_TOKEN or GITHUB_TEST_REPO not configured in .env"
        })

    # 2. Slack
    slack_token = os.getenv("SLACK_BOT_TOKEN")
    slack_test_channel = os.getenv("SLACK_TEST_CHANNEL")
    slack_endpoint = "https://slack.com/api"
    if slack_token and slack_test_channel:
        t0 = time.perf_counter()
        adapter = LiveSlackAdapter(token=slack_token)
        try:
            resp = await adapter.search("", channel=slack_test_channel)
            lat = (time.perf_counter() - t0) * 1000.0
            found = len(resp.data.get("messages", [])) if resp.data else 0
            if resp.success:
                status = ConnectorStatus.LIVE_VERIFIED.value
                result = f"Confirmed channel read ({found} messages) on #{slack_test_channel}"
            else:
                status = ConnectorStatus.UNAVAILABLE.value
                result = f"API Error: {resp.error}"
            results.append({
                "app": "Slack",
                "status": status,
                "endpoint": slack_endpoint,
                "op": "search_messages",
                "resource": f"#{slack_test_channel}",
                "latency": f"{lat:.1f}ms",
                "result": result
            })
        except Exception as e:
            results.append({
                "app": "Slack",
                "status": ConnectorStatus.UNAVAILABLE.value,
                "endpoint": slack_endpoint,
                "op": "search_messages",
                "resource": f"#{slack_test_channel}" if slack_test_channel else "N/A",
                "latency": "N/A",
                "result": f"Connection Exception: {str(e)}"
            })
    else:
        results.append({
            "app": "Slack",
            "status": ConnectorStatus.AUTHENTICATION_REQUIRED.value,
            "endpoint": slack_endpoint,
            "op": "search_messages",
            "resource": f"#{slack_test_channel}" if slack_test_channel else "UNCONFIGURED (Set SLACK_TEST_CHANNEL)",
            "latency": "N/A",
            "result": "SKIPPED - SLACK_BOT_TOKEN or SLACK_TEST_CHANNEL not configured in .env"
        })

    # 3. Jira
    jira_url = os.getenv("JIRA_URL") or os.getenv("JIRA_BASE_URL")
    jira_user = os.getenv("JIRA_USER_EMAIL")
    jira_token = os.getenv("JIRA_API_TOKEN")
    jira_test_project = os.getenv("JIRA_TEST_PROJECT")
    jira_endpoint = jira_url or "https://your-domain.atlassian.net/rest/api/3"
    if jira_url and jira_user and jira_token and jira_test_project:
        t0 = time.perf_counter()
        adapter = LiveJiraAdapter(base_url=jira_url, email=jira_user, api_token=jira_token)
        try:
            resp = await adapter.search(f"project = {jira_test_project}")
            lat = (time.perf_counter() - t0) * 1000.0
            found = len(resp.data.get("issues", [])) if resp.data else 0
            if resp.success:
                status = ConnectorStatus.LIVE_VERIFIED.value
                result = f"Confirmed {found} issues in project {jira_test_project}"
            else:
                status = ConnectorStatus.UNAVAILABLE.value
                result = f"API Error: {resp.error}"
            results.append({
                "app": "Jira",
                "status": status,
                "endpoint": jira_endpoint,
                "op": "search_issues",
                "resource": f"Project: {jira_test_project}",
                "latency": f"{lat:.1f}ms",
                "result": result
            })
        except Exception as e:
            results.append({
                "app": "Jira",
                "status": ConnectorStatus.UNAVAILABLE.value,
                "endpoint": jira_endpoint,
                "op": "search_issues",
                "resource": f"Project: {jira_test_project}",
                "latency": "N/A",
                "result": f"Connection Exception: {str(e)}"
            })
    else:
        results.append({
            "app": "Jira",
            "status": ConnectorStatus.AUTHENTICATION_REQUIRED.value,
            "endpoint": jira_endpoint,
            "op": "search_issues",
            "resource": f"Project: {jira_test_project}" if jira_test_project else "UNCONFIGURED (Set JIRA_TEST_PROJECT)",
            "latency": "N/A",
            "result": "SKIPPED - JIRA_URL/USER/TOKEN or JIRA_TEST_PROJECT not configured in .env"
        })

    return results


def print_smoke_report(results: list[Dict[str, Any]]):
    print("\n---")
    print("\n## RECLAIM LIVE CONNECTIVITY\n")
    for r in results:
        print(f"{r['app']}")
        print(f"Status: {r['status']}")
        print(f"Endpoint: {r['endpoint']}")
        print(f"Operation: {r['op']}")
        print(f"Resource: {r['resource']}")
        print(f"Latency: {r['latency']}")
        print(f"Result: {r['result']}\n")
    print("---")


def main():
    res = asyncio.run(probe_live_adapters())
    print_smoke_report(res)


if __name__ == "__main__":
    main()
