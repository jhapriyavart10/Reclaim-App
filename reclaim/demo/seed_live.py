"""
RECLAIM Live Demo Seeding Script.
Creates a small, clearly marked, reversible live incident scenario using only dedicated test resources.
Writes artifacts/live_demo_seed.json for deterministic tracking and cleanup.
"""

import asyncio
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from reclaim.demo.safety import validate_live_test_resources
from reclaim.adapters.live.github import LiveGitHubAdapter
from reclaim.adapters.live.slack import LiveSlackAdapter
from reclaim.adapters.live.jira import LiveJiraAdapter


MANIFEST_PATH = Path("artifacts/live_demo_seed.json")


async def seed_live_incident(run_id: str = None) -> dict:
    if not run_id:
        run_id = f"RECLAIM-LIVE-{uuid.uuid4().hex[:8]}"

    print(f"\n==================================================")
    print(f"       RECLAIM LIVE DEMO SEEDING PIPELINE")
    print(f"==================================================")
    print(f"Run ID: {run_id}")

    # 1. Safety Check
    is_safe, code, details = validate_live_test_resources()
    if not is_safe:
        print(f"\n[SAFETY ERROR] {code}: {details.get('error')}")
        manifest = {
            "status": code,
            "error": details.get("error"),
            "run_id": run_id,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        return manifest

    gh_repo = os.getenv("GITHUB_TEST_REPO")
    slack_channel = os.getenv("SLACK_TEST_CHANNEL")
    jira_project = os.getenv("JIRA_TEST_PROJECT")

    manifest = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "resources": {
            "github": {
                "repository": gh_repo,
                "issue_number": None,
                "issue_url": None
            },
            "slack": {
                "channel_id": slack_channel,
                "message_ts": []
            },
            "jira": {
                "project_key": jira_project,
                "issue_key": None,
                "issue_id": None
            }
        },
        "cleanup_completed": False
    }

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 2. Seed GitHub Issue
    print(f"\n[1/3] Seeding GitHub live test issue in {gh_repo}...")
    gh_adapter = LiveGitHubAdapter()
    gh_title = f"[RECLAIM-LIVE][RUN:{run_id}] Regression candidate: v2.4.1 serializer payload schema mismatch"
    gh_body = (
        f"Automated incident test evidence for {run_id}.\n\n"
        f"**Technical Detail**: Commit hash introduces strict marshmallow/pydantic schema validation "
        f"on `/v2/data-sync` endpoint. Payloads containing extra legacy metadata fields now raise "
        f"`ValidationError` and return HTTP 500 to enterprise clients."
    )
    gh_resp, gh_receipt = await gh_adapter.create(
        resource_type="issue",
        payload={
            "repo": gh_repo,
            "title": gh_title,
            "body": gh_body,
            "labels": ["reclaim", "test", "regression"]
        },
        idempotency_key=f"seed_gh_{run_id}"
    )
    if not gh_resp.success:
        raise RuntimeError(f"GitHub live seeding failed: {gh_resp.error}")

    gh_issue_num = gh_resp.data.get("issue_number")
    gh_issue_url = gh_resp.data.get("url")
    manifest["resources"]["github"]["issue_number"] = gh_issue_num
    manifest["resources"]["github"]["issue_url"] = gh_issue_url
    print(f"  -> Created GitHub Issue #{gh_issue_num}: {gh_title}")

    # Readback verification
    gh_read = await gh_adapter.read(resource_id=str(gh_issue_num), repo=gh_repo)
    if not gh_read.success:
        raise RuntimeError(f"GitHub seed verification readback failed: {gh_read.error}")
    print(f"  -> Verified GitHub readback: state='{gh_read.data.get('state')}'")

    # 3. Seed Slack Message
    print(f"\n[2/3] Seeding Slack live incident message in #{slack_channel}...")
    slack_adapter = LiveSlackAdapter()
    slack_text = (
        f"[RECLAIM-LIVE][RUN:{run_id}] :rotating_light: P1 Incident Alert: "
        f"Acme Corp telemetry reports 500 errors on /v2/data-sync endpoint after recent serializer deployment."
    )
    slack_resp, slack_receipt = await slack_adapter.create(
        resource_type="message",
        payload={"channel": slack_channel, "text": slack_text},
        idempotency_key=f"seed_slack_{run_id}"
    )
    if not slack_resp.success:
        raise RuntimeError(f"Slack live seeding failed: {slack_resp.error}")

    slack_ts = slack_resp.data.get("ts")
    manifest["resources"]["slack"]["message_ts"].append(slack_ts)
    print(f"  -> Posted Slack message with ts: {slack_ts}")

    # Readback verification
    slack_read = await slack_adapter.search(query="", channel_id=slack_channel, limit=10)
    if not slack_read.success or not any(m.get("ts") == slack_ts for m in slack_read.data.get("messages", [])):
        raise RuntimeError("Slack seed verification readback failed: message not found in channel history.")
    print(f"  -> Verified Slack readback: message confirmed in history")

    # 4. Seed Jira Issue (New Issue created exclusively for this run)
    print(f"\n[3/3] Seeding Jira live incident ticket in project {jira_project}...")
    jira_adapter = LiveJiraAdapter()
    jira_summary = f"[RECLAIM-LIVE][RUN:{run_id}] Investigate API v2 sync 500 errors"
    jira_desc = (
        f"Incident run {run_id}. Client reports intermittent 500 Internal Server Errors on /v2/data-sync. "
        f"Initial priority logged as Medium. Requires root-cause triage and escalation."
    )
    jira_resp, jira_receipt = await jira_adapter.create(
        resource_type="issue",
        payload={
            "project": jira_project,
            "summary": jira_summary,
            "description": jira_desc,
            "issue_type": "Task"
        },
        idempotency_key=f"seed_jira_{run_id}"
    )
    if not jira_resp.success:
        raise RuntimeError(f"Jira live seeding failed: {jira_resp.error}")

    jira_key = jira_resp.data.get("key")
    jira_id = jira_resp.data.get("id")
    manifest["resources"]["jira"]["issue_key"] = jira_key
    manifest["resources"]["jira"]["issue_id"] = jira_id
    print(f"  -> Created Jira Issue {jira_key} (id: {jira_id}): {jira_summary}")

    # Readback verification
    jira_read = await jira_adapter.read(resource_id=jira_key)
    if not jira_read.success:
        raise RuntimeError(f"Jira seed verification readback failed: {jira_read.error}")
    print(f"  -> Verified Jira readback: key='{jira_key}', summary='{jira_read.data.get('fields', {}).get('summary')}'")

    # 5. Write Manifest
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n[SEED COMPLETE] Manifest saved to {MANIFEST_PATH}")
    print(json.dumps(manifest, indent=2))
    return manifest


def main():
    try:
        res = asyncio.run(seed_live_incident())
        if res.get("status") and res.get("status").startswith("REFUSED"):
            sys.exit(1)
    except Exception as e:
        print(f"\n[SEED FATAL] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
