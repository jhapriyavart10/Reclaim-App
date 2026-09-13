"""
RECLAIM Live Demo Cleanup Script.
Strictly restores and deletes only the exact live resources recorded in artifacts/live_demo_seed.json.
Refuses to run if the manifest is missing, malformed, or references untracked resources.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from reclaim.adapters.live.github import LiveGitHubAdapter
from reclaim.adapters.live.slack import LiveSlackAdapter
from reclaim.adapters.live.jira import LiveJiraAdapter


MANIFEST_PATH = Path("artifacts/live_demo_seed.json")


async def cleanup_live_resources() -> dict:
    print(f"\n==================================================")
    print(f"       RECLAIM LIVE DEMO CLEANUP PIPELINE")
    print(f"==================================================")

    if not MANIFEST_PATH.exists():
        print(f"[CLEANUP REFUSED] Manifest not found at {MANIFEST_PATH}. Cannot safely identify run resources.")
        return {"status": "REFUSED_MISSING_MANIFEST", "error": f"Manifest {MANIFEST_PATH} not found"}

    try:
        with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception as e:
        print(f"[CLEANUP REFUSED] Corrupted manifest: {e}")
        return {"status": "REFUSED_MALFORMED_MANIFEST", "error": str(e)}

    run_id = manifest.get("run_id")
    if not run_id or not run_id.startswith("RECLAIM-LIVE-"):
        print(f"[CLEANUP REFUSED] Invalid or missing run_id in manifest: {run_id}")
        return {"status": "REFUSED_INVALID_RUN_ID", "error": f"Invalid run_id {run_id}"}

    resources = manifest.get("resources", {})
    gh_info = resources.get("github", {})
    slack_info = resources.get("slack", {})
    jira_info = resources.get("jira", {})

    print(f"Target Run ID: {run_id}")
    cleanup_results = {
        "run_id": run_id,
        "cleaned_at": datetime.now(timezone.utc).isoformat(),
        "github": None,
        "slack": None,
        "jira": None,
        "all_verified": False
    }

    # 1. Clean GitHub Issue
    gh_repo = gh_info.get("repository")
    gh_issue_num = gh_info.get("issue_number")
    if gh_repo and gh_issue_num:
        print(f"\n[1/3] Restoring GitHub Issue #{gh_issue_num} in {gh_repo}...")
        gh_adapter = LiveGitHubAdapter()
        # Verify title contains run_id before closing
        gh_read = await gh_adapter.read(resource_id=str(gh_issue_num), repo=gh_repo)
        if gh_read.success:
            title = gh_read.data.get("title", "")
            if run_id not in title:
                print(f"  [SAFETY WARNING] Issue #{gh_issue_num} does not contain {run_id}. Skipping mutation.")
                cleanup_results["github"] = {"status": "SKIPPED_UNMATCHED_TAG"}
            else:
                upd_resp, _ = await gh_adapter.update(
                    resource_id=str(gh_issue_num),
                    payload={"state": "closed"},
                    idempotency_key=f"cleanup_gh_{run_id}",
                    repo=gh_repo
                )
                # Verify closed
                verify_read = await gh_adapter.read(resource_id=str(gh_issue_num), repo=gh_repo)
                is_closed = verify_read.data.get("state") == "closed" if verify_read.success else False
                cleanup_results["github"] = {
                    "issue_number": gh_issue_num,
                    "closed": is_closed,
                    "verified": is_closed
                }
                print(f"  -> GitHub Issue #{gh_issue_num} closed and verified: {is_closed}")
        else:
            cleanup_results["github"] = {"status": "NOT_FOUND_OR_ERROR", "error": gh_read.error}

    # 2. Clean Slack Messages
    slack_channel = slack_info.get("channel_id")
    message_ts_list = slack_info.get("message_ts", [])
    if slack_channel and message_ts_list:
        print(f"\n[2/3] Deleting Slack messages in #{slack_channel}...")
        slack_adapter = LiveSlackAdapter()
        deleted_count = 0
        for ts in message_ts_list:
            del_resp = await slack_adapter.delete(channel=slack_channel, ts=ts)
            if del_resp.success:
                deleted_count += 1
                print(f"  -> Deleted message ts: {ts}")
            else:
                print(f"  [WARNING] Could not delete ts {ts}: {del_resp.error}")

        # Verification
        hist = await slack_adapter.search(query="", channel_id=slack_channel, limit=20)
        hist_msgs = hist.data.get("messages", []) if hist.success else []
        persisting = [ts for ts in message_ts_list if any(m.get("ts") == ts for m in hist_msgs)]
        cleanup_results["slack"] = {
            "channel_id": slack_channel,
            "deleted_count": deleted_count,
            "persisting_count": len(persisting),
            "verified": len(persisting) == 0
        }
        print(f"  -> Slack cleanup verified: {len(persisting) == 0}")

    # 3. Clean Jira Issue (Delete the newly created run ticket)
    jira_key = jira_info.get("issue_key")
    if jira_key:
        print(f"\n[3/3] Deleting run-created Jira ticket {jira_key}...")
        jira_adapter = LiveJiraAdapter()
        # Verify title contains run_id before deleting
        jira_read = await jira_adapter.read(resource_id=jira_key)
        if jira_read.success:
            summary = jira_read.data.get("fields", {}).get("summary", "")
            if run_id not in summary:
                print(f"  [SAFETY WARNING] Jira issue {jira_key} does not contain {run_id}. Refusing delete.")
                cleanup_results["jira"] = {"status": "SKIPPED_UNMATCHED_TAG"}
            else:
                del_resp = await jira_adapter.delete(resource_id=jira_key)
                # Verify deletion
                verify_read = await jira_adapter.read(resource_id=jira_key)
                is_deleted = not verify_read.success or verify_read.error and "404" in str(verify_read.error)
                cleanup_results["jira"] = {
                    "issue_key": jira_key,
                    "deleted": del_resp.success,
                    "verified": is_deleted
                }
                print(f"  -> Jira issue {jira_key} deleted and verified: {is_deleted}")
        else:
            cleanup_results["jira"] = {"status": "NOT_FOUND", "verified": True}

    all_verified = (
        (cleanup_results["github"] is None or cleanup_results["github"].get("verified", False)) and
        (cleanup_results["slack"] is None or cleanup_results["slack"].get("verified", False)) and
        (cleanup_results["jira"] is None or cleanup_results["jira"].get("verified", False))
    )
    cleanup_results["all_verified"] = all_verified

    manifest["cleanup_completed"] = all_verified
    manifest["cleaned_at"] = cleanup_results["cleaned_at"]
    manifest["cleanup_details"] = cleanup_results

    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"\n[CLEANUP COMPLETE] Manifest updated at {MANIFEST_PATH}")
    print(json.dumps(cleanup_results, indent=2))
    return cleanup_results


def main():
    res = asyncio.run(cleanup_live_resources())
    if res.get("status") and res.get("status").startswith("REFUSED"):
        sys.exit(1)


if __name__ == "__main__":
    main()
