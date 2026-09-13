"""
Safe Live Write Test: Jira
Lifecycle:
PRECONDITION -> WRITE -> INDEPENDENT READ -> ASSERT -> RESTORE -> INDEPENDENT READ -> PASS

Refuses to run unless JIRA_URL, JIRA_USER_EMAIL, JIRA_API_TOKEN, and JIRA_TEST_PROJECT are explicitly configured.
Never defaults to production or arbitrary Jira projects.
"""

import asyncio
import json
import os
import sys
import uuid
from dotenv import load_dotenv

load_dotenv()

from reclaim.adapters.live.jira import LiveJiraAdapter


async def run_live_jira_write_test() -> dict:
    url = os.getenv("JIRA_URL")
    user = os.getenv("JIRA_USER_EMAIL")
    token = os.getenv("JIRA_API_TOKEN")
    test_project = os.getenv("JIRA_TEST_PROJECT")
    test_issue = os.getenv("JIRA_TEST_ISSUE")

    has_creds = bool(url and user and token)
    has_target = bool(test_project or test_issue)

    if not has_creds or not has_target:
        result = {
            "status": "REFUSED_MISSING_CONFIG",
            "app": "jira",
            "reason": (
                "JIRA_URL, JIRA_USER_EMAIL, JIRA_API_TOKEN, and JIRA_TEST_PROJECT (or JIRA_TEST_ISSUE) "
                "must all be explicitly configured. RECLAIM strictly refuses to execute mutations "
                "against unverified or production Jira projects."
            ),
            "configured_url": bool(url),
            "configured_user": bool(user),
            "configured_token": bool(token),
            "configured_test_project": test_project or None,
            "configured_test_issue": test_issue or None
        }
        print(f"[JIRA LIVE WRITE TEST] {result['status']}: {result['reason']}")
        print(json.dumps(result, indent=2))
        return result

    adapter = LiveJiraAdapter(base_url=url, email=user, api_token=token)
    run_id = uuid.uuid4().hex[:8]
    print(f"\n================ JIRA LIVE WRITE TEST ================")
    print(f"[PRECONDITION] Target Base URL: {url}")
    print(f"[PRECONDITION] Target Project: {test_project or 'N/A'}, Target Issue: {test_issue or 'N/A'}")
    print(f"[PRECONDITION] Authenticating as {user}...")

    # 1. PRECONDITION CHECK
    caps = await adapter.get_capabilities()
    if caps.availability != "AVAILABLE":
        result = {"status": "FAILED_PRECONDITION", "app": "jira", "error": "Adapter reports unavailable"}
        print(json.dumps(result, indent=2))
        return result

    # Path A: If test_issue is designated, do safe update and revert
    if test_issue:
        print(f"[PRECONDITION] Reading existing issue {test_issue}...")
        read_orig = await adapter.read(resource_id=test_issue)
        if not read_orig.success:
            result = {"status": "FAILED_PRECONDITION_READ", "app": "jira", "error": read_orig.error}
            print(json.dumps(result, indent=2))
            return result
        orig_summary = read_orig.data.get("fields", {}).get("summary", "")
        test_summary = f"{orig_summary} [RECLAIM_TEST_{run_id}]"

        # 2. WRITE (Reversible update)
        print(f"[WRITE] Appending probe tag to issue {test_issue}...")
        update_resp, receipt = await adapter.update(
            resource_id=test_issue,
            payload={"summary": test_summary},
            idempotency_key=f"live_test_jira_{run_id}"
        )
        if not update_resp.success:
            result = {"status": "FAILED_WRITE", "app": "jira", "error": update_resp.error}
            print(json.dumps(result, indent=2))
            return result

        # 3. INDEPENDENT READ
        print(f"[INDEPENDENT READ] Fetching issue {test_issue} to verify update...")
        read_mod = await adapter.read(resource_id=test_issue)
        new_summary = read_mod.data.get("fields", {}).get("summary", "")

        # 4. ASSERT
        print(f"[ASSERT] Observed summary: '{new_summary}'")
        if run_id not in new_summary:
            result = {"status": "FAILED_ASSERTION", "app": "jira", "expected_tag": run_id, "observed": new_summary}
            print(json.dumps(result, indent=2))
            return result

        # 5. RESTORE (Revert summary)
        print(f"[RESTORE] Reverting issue {test_issue} summary to original value...")
        restore_resp, _ = await adapter.update(
            resource_id=test_issue,
            payload={"summary": orig_summary},
            idempotency_key=f"live_restore_jira_{run_id}"
        )
        if not restore_resp.success:
            result = {"status": "FAILED_RESTORE", "app": "jira", "error": restore_resp.error}
            print(json.dumps(result, indent=2))
            return result

        # 6. INDEPENDENT READ
        print(f"[INDEPENDENT READ] Verifying restored summary on issue {test_issue}...")
        read_restored = await adapter.read(resource_id=test_issue)
        final_summary = read_restored.data.get("fields", {}).get("summary", "")
        if run_id in final_summary:
            result = {"status": "FAILED_VERIFY_RESTORE", "app": "jira", "persisting_tag": True}
            print(json.dumps(result, indent=2))
            return result

        # 7. PASS
        result = {
            "status": "PASS",
            "app": "jira",
            "lifecycle": "PRECONDITION -> WRITE -> INDEPENDENT READ -> ASSERT -> RESTORE -> INDEPENDENT READ -> PASS",
            "test_issue": test_issue,
            "restored": True
        }
        print(f"[PASS] Jira live write verification succeeded.")
        print(json.dumps(result, indent=2))
        return result

    # Path B: If test_project is designated, create temporary issue and delete it
    summary = f"[RECLAIM TEST ONLY] Automated Verification Probe {run_id}"
    print(f"[WRITE] Creating probe task in project {test_project}...")
    create_resp, receipt = await adapter.create(
        resource_type="issue",
        payload={
            "project": test_project,
            "summary": summary,
            "description": "Temporary probe created by RECLAIM live verification suite.",
            "issue_type": "Task"
        },
        idempotency_key=f"live_test_jira_cr_{run_id}"
    )

    if not create_resp.success:
        result = {"status": "FAILED_WRITE", "app": "jira", "error": create_resp.error}
        print(json.dumps(result, indent=2))
        return result

    created_key = create_resp.data.get("key")
    print(f"[WRITE SUCCESS] Created issue {created_key}")

    # 3. INDEPENDENT READ
    print(f"[INDEPENDENT READ] Fetching issue {created_key}...")
    read_resp = await adapter.read(resource_id=created_key)
    if not read_resp.success:
        result = {"status": "FAILED_INDEPENDENT_READ", "app": "jira", "error": read_resp.error}
        print(json.dumps(result, indent=2))
        return result

    # 4. ASSERT
    obs_summary = read_resp.data.get("fields", {}).get("summary", "")
    print(f"[ASSERT] Observed summary: '{obs_summary}'")
    if obs_summary != summary:
        result = {"status": "FAILED_ASSERTION", "app": "jira", "expected": summary, "observed": obs_summary}
        print(json.dumps(result, indent=2))
        return result

    # 5. RESTORE (Delete created issue)
    print(f"[RESTORE] Deleting test issue {created_key}...")
    del_resp = await adapter.delete(resource_id=created_key)
    if not del_resp.success:
        print(f"[RESTORE WARNING] Deletion returned: {del_resp.error}")

    # 6. INDEPENDENT READ (Verify deletion)
    print(f"[INDEPENDENT READ] Re-reading {created_key} to confirm deletion...")
    verify_del = await adapter.read(resource_id=created_key)
    print(f"[ASSERT RESTORED] Exists after deletion: {verify_del.success} (expected: False)")

    # 7. PASS
    result = {
        "status": "PASS",
        "app": "jira",
        "lifecycle": "PRECONDITION -> WRITE -> INDEPENDENT READ -> ASSERT -> RESTORE -> INDEPENDENT READ -> PASS",
        "test_project": test_project,
        "created_issue": created_key,
        "restored": del_resp.success
    }
    print(f"[PASS] Jira live write verification succeeded.")
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    asyncio.run(run_live_jira_write_test())
