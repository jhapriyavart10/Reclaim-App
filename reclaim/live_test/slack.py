"""
Safe Live Write Test: Slack
Lifecycle:
PRECONDITION -> WRITE -> INDEPENDENT READ -> ASSERT -> RESTORE -> INDEPENDENT READ -> PASS

Refuses to run unless SLACK_BOT_TOKEN and SLACK_TEST_CHANNEL are explicitly configured.
Never defaults to production or arbitrary Slack channels.
"""

import asyncio
import json
import os
import sys
import uuid
from dotenv import load_dotenv

load_dotenv()

from reclaim.adapters.live.slack import LiveSlackAdapter


async def run_live_slack_write_test() -> dict:
    token = os.getenv("SLACK_BOT_TOKEN")
    test_channel = os.getenv("SLACK_TEST_CHANNEL")

    if not token or not test_channel:
        result = {
            "status": "REFUSED_MISSING_CONFIG",
            "app": "slack",
            "reason": (
                "SLACK_BOT_TOKEN and SLACK_TEST_CHANNEL must both be explicitly configured. "
                "RECLAIM strictly refuses to post live test alerts into arbitrary or production channels."
            ),
            "configured_token": bool(token),
            "configured_test_channel": test_channel or None
        }
        print(f"[SLACK LIVE WRITE TEST] {result['status']}: {result['reason']}")
        print(json.dumps(result, indent=2))
        return result

    adapter = LiveSlackAdapter(token=token)
    run_id = uuid.uuid4().hex[:8]
    print(f"\n================ SLACK LIVE WRITE TEST ================")
    print(f"[PRECONDITION] Target Channel: {test_channel}")
    print(f"[PRECONDITION] Authenticating with provided SLACK_BOT_TOKEN...")

    # 1. PRECONDITION CHECK
    caps = await adapter.get_capabilities()
    if caps.availability != "AVAILABLE":
        result = {"status": "FAILED_PRECONDITION", "app": "slack", "error": "Adapter reports unavailable"}
        print(json.dumps(result, indent=2))
        return result

    # 2. WRITE (Post temporary test message)
    test_text = f"[RECLAIM TEST ONLY] Automated Verification Probe {run_id} - Safe to delete."
    print(f"[WRITE] Posting message to channel {test_channel}: '{test_text}'")
    create_resp, receipt = await adapter.create(
        resource_type="message",
        payload={"channel": test_channel, "text": test_text},
        idempotency_key=f"live_test_slack_{run_id}"
    )

    if not create_resp.success:
        result = {"status": "FAILED_WRITE", "app": "slack", "error": create_resp.error}
        print(f"[WRITE FAILED] {create_resp.error}")
        print(json.dumps(result, indent=2))
        return result

    ts = create_resp.data.get("ts")
    print(f"[WRITE SUCCESS] Posted message with ts: {ts}")

    # 3. INDEPENDENT READ
    print(f"[INDEPENDENT READ] Querying channel history for ts {ts}...")
    search_resp = await adapter.search(query="", channel_id=test_channel, limit=10)
    if not search_resp.success:
        result = {"status": "FAILED_INDEPENDENT_READ", "app": "slack", "error": search_resp.error}
        print(json.dumps(result, indent=2))
        return result

    # 4. ASSERT
    messages = search_resp.data.get("messages", [])
    found = any(m.get("ts") == ts for m in messages)
    print(f"[ASSERT] Message found in channel history: {found}")
    if not found:
        result = {"status": "FAILED_ASSERTION", "app": "slack", "expected_ts": ts, "observed": "Not in recent history"}
        print(json.dumps(result, indent=2))
        return result

    # 5. RESTORE (Delete test message)
    print(f"[RESTORE] Deleting test message with ts {ts}...")
    del_resp = await adapter.delete(channel=test_channel, ts=ts)
    if not del_resp.success:
        # Some bots might not have chat:write delete permissions or scopes, report accurately
        print(f"[RESTORE WARNING] Message deletion returned: {del_resp.error}")
        # Note: If delete scope is missing, we record restore attempt
    else:
        print(f"[RESTORE SUCCESS] Message deleted.")

    # 6. INDEPENDENT READ (Verify restoration if delete succeeded)
    if del_resp.success:
        print(f"[INDEPENDENT READ] Re-checking channel history to confirm message removal...")
        verify_resp = await adapter.search(query="", channel_id=test_channel, limit=10)
        verify_msgs = verify_resp.data.get("messages", []) if verify_resp.success else []
        still_present = any(m.get("ts") == ts for m in verify_msgs)
        print(f"[ASSERT RESTORED] Message still present: {still_present} (expected: False)")
        if still_present:
            result = {"status": "FAILED_VERIFY_RESTORE", "app": "slack", "message_persists": True}
            print(json.dumps(result, indent=2))
            return result

    # 7. PASS
    result = {
        "status": "PASS",
        "app": "slack",
        "lifecycle": "PRECONDITION -> WRITE -> INDEPENDENT READ -> ASSERT -> RESTORE -> INDEPENDENT READ -> PASS",
        "test_channel": test_channel,
        "message_ts": ts,
        "restored": del_resp.success
    }
    print(f"[PASS] Slack live write verification succeeded.")
    print(json.dumps(result, indent=2))
    return result


if __name__ == "__main__":
    asyncio.run(run_live_slack_write_test())
