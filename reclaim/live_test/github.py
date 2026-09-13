"""
Safe Live Write Test: GitHub
Lifecycle:
PRECONDITION -> WRITE -> INDEPENDENT READ -> ASSERT -> RESTORE -> INDEPENDENT READ -> PASS

Refuses to run unless GITHUB_TOKEN and GITHUB_TEST_REPO are explicitly configured.
Never defaults to production or arbitrary repositories.
"""

import asyncio
import json
import os
import sys
import uuid
from dotenv import load_dotenv

load_dotenv()

from reclaim.adapters.live.github import LiveGitHubAdapter


async def run_live_github_write_test() -> dict:
    token = os.getenv("GITHUB_TOKEN")
    test_repo = os.getenv("GITHUB_TEST_REPO")

    if not token or not test_repo:
        result = {
            "status": "REFUSED_MISSING_CONFIG",
            "app": "github",
            "reason": (
                "GITHUB_TOKEN and GITHUB_TEST_REPO must both be explicitly configured. "
                "RECLAIM strictly refuses to execute live write tests against default or production repositories."
            ),
            "configured_token": bool(token),
            "configured_test_repo": test_repo or None
        }
        print(f"[GITHUB LIVE WRITE TEST] {result['status']}: {result['reason']}")
        print(json.dumps(result, indent=2))
        return result

    adapter = LiveGitHubAdapter(token=token)
    run_id = uuid.uuid4().hex[:8]
    print(f"\n================ GITHUB LIVE WRITE TEST ================")
    print(f"[PRECONDITION] Target Repo: {test_repo}")
    print(f"[PRECONDITION] Authenticating with provided GITHUB_TOKEN...")

    # 1. PRECONDITION CHECK (Read repo info)
    caps = await adapter.get_capabilities()
    if caps.availability != "AVAILABLE":
        result = {"status": "FAILED_PRECONDITION", "app": "github", "error": "Adapter reports unavailable"}
        print(json.dumps(result, indent=2))
        return result

    # 2. WRITE (Create minimal test issue)
    title = f"[RECLAIM TEST ONLY] Automated Verification Probe {run_id}"
    body = (
        "Temporary probe emitted by RECLAIM live verification suite.\n"
        "This is an automated lifecycle test. It will be verified and closed immediately."
    )
    print(f"[WRITE] Creating test issue in {test_repo}: '{title}'")
    create_resp, receipt = await adapter.create(
        resource_type="issue",
        payload={"repo": test_repo, "title": title, "body": body, "labels": ["reclaim", "test"]},
        idempotency_key=f"live_test_gh_{run_id}"
    )

    if not create_resp.success:
        result = {"status": "FAILED_WRITE", "app": "github", "error": create_resp.error}
        print(f"[WRITE FAILED] {create_resp.error}")
        print(json.dumps(result, indent=2))
        return result

    issue_number = str(create_resp.data.get("issue_number"))
    print(f"[WRITE SUCCESS] Created issue #{issue_number}")

    # 3. INDEPENDENT READ
    print(f"[INDEPENDENT READ] Fetching issue #{issue_number} from GitHub API...")
    read_resp = await adapter.read(resource_id=issue_number, repo=test_repo)
    if not read_resp.success:
        result = {"status": "FAILED_INDEPENDENT_READ", "app": "github", "error": read_resp.error}
        print(json.dumps(result, indent=2))
        return result

    # 4. ASSERT
    observed_state = read_resp.data.get("state")
    print(f"[ASSERT] Observed state: '{observed_state}' (expected: 'open')")
    if observed_state != "open":
        result = {"status": "FAILED_ASSERTION", "app": "github", "expected": "open", "observed": observed_state}
        print(json.dumps(result, indent=2))
        return result

    # 5. RESTORE (Close issue)
    print(f"[RESTORE] Closing test issue #{issue_number}...")
    restore_resp, _ = await adapter.update(
        resource_id=issue_number,
        payload={"repo": test_repo, "state": "closed"},
        idempotency_key=f"live_restore_gh_{run_id}",
        repo=test_repo
    )
    if not restore_resp.success:
        result = {"status": "FAILED_RESTORE", "app": "github", "error": restore_resp.error, "issue": issue_number}
        print(json.dumps(result, indent=2))
        return result

    # 6. INDEPENDENT READ (Verify restoration)
    print(f"[INDEPENDENT READ] Re-reading issue #{issue_number} to verify closed state...")
    verify_read = await adapter.read(resource_id=issue_number, repo=test_repo)
    final_state = verify_read.data.get("state") if verify_read.success else "unknown"
    print(f"[ASSERT RESTORED] Final state: '{final_state}' (expected: 'closed')")

    if final_state != "closed":
        result = {"status": "FAILED_VERIFY_RESTORE", "app": "github", "final_state": final_state}
        print(json.dumps(result, indent=2))
        return result

    # 7. PASS
    result = {
        "status": "PASS",
        "app": "github",
        "lifecycle": "PRECONDITION -> WRITE -> INDEPENDENT READ -> ASSERT -> RESTORE -> INDEPENDENT READ -> PASS",
        "test_repo": test_repo,
        "issue_number": issue_number,
        "restored_state": "closed"
    }
    print(f"[PASS] GitHub live write verification succeeded.")
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    asyncio.run(run_live_github_write_test())
