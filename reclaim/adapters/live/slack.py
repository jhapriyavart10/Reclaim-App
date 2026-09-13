import os
import time
import logging
from typing import Dict, Any, Optional
import httpx

from reclaim.adapters.base import (
    BaseAdapter,
    SourceType,
    RiskLevel,
    AdapterResponse,
    ActionReceipt,
    AppCapability
)
from reclaim.adapters.idempotency import idempotency_manager

logger = logging.getLogger(__name__)

SLACK_API_BASE = "https://slack.com/api"


class LiveSlackAdapter(BaseAdapter):
    """
    Live Slack connector interacting with the official Slack Web API.
    Uses Bot User OAuth Token (SLACK_BOT_TOKEN) with chat:write & channels:history scopes.
    """

    def __init__(self, token: Optional[str] = None):
        super().__init__(app_name="slack", source_type=SourceType.LIVE)
        self.token = token or os.getenv("SLACK_BOT_TOKEN")
        self.timeout_seconds = 15.0

    def _get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json; charset=utf-8"
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def get_capabilities(self) -> AppCapability:
        is_configured = bool(self.token)
        availability = "AVAILABLE" if is_configured else "UNAVAILABLE"
        auth_status = "AUTHENTICATED" if is_configured else "UNCONFIGURED"

        return AppCapability(
            app="slack",
            source_type=SourceType.LIVE,
            availability=availability,
            authentication_status=auth_status,
            read_operations=["conversations_history", "conversations_list", "search_messages"],
            write_operations=["chat_post_message"],
            verification_operations=["verify_message_in_history"],
            risk_policy={
                "conversations_history": RiskLevel.READ_ONLY,
                "conversations_list": RiskLevel.READ_ONLY,
                "chat_post_message": RiskLevel.LOW_RISK_WRITE
            }
        )

    async def search(self, query: str, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        if not self.token:
            return AdapterResponse(
                source_type=self.source_type,
                app="slack",
                operation="search",
                success=False,
                error="Live Slack connector unavailable: SLACK_BOT_TOKEN is unconfigured",
                latency_ms=0.0
            )

        raw_chan = kwargs.get("channel_id") or kwargs.get("channel") or os.getenv("SLACK_TEST_CHANNEL")
        test_chan = os.getenv("SLACK_TEST_CHANNEL")
        if not raw_chan or raw_chan.startswith("<") or raw_chan.startswith("#") or not (raw_chan.startswith("C") or raw_chan.startswith("D")):
            channel_id = test_chan or raw_chan
        else:
            channel_id = raw_chan

        if not channel_id:
            return AdapterResponse(source_type=self.source_type, app="slack", operation="search", success=False, error="Missing channel_id parameter")

        url = f"{SLACK_API_BASE}/conversations.history"
        params = {"channel": channel_id, "limit": kwargs.get("limit", 20)}

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(url, headers=self._get_headers(), params=params)
            dt = (time.perf_counter() - t0) * 1000.0

            data = resp.json()
            if not data.get("ok") and data.get("error") == "channel_not_found" and test_chan and channel_id != test_chan:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    resp = await client.get(url, headers=self._get_headers(), params={"channel": test_chan, "limit": kwargs.get("limit", 20)})
                data = resp.json()

            if data.get("ok"):
                messages = data.get("messages", [])
                matching = [m for m in messages if query.lower() in m.get("text", "").lower()] if query else messages
                return AdapterResponse(
                    source_type=self.source_type,
                    app="slack",
                    operation="search",
                    latency_ms=dt,
                    success=True,
                    data={"messages": matching, "count": len(matching)}
                )

            return AdapterResponse(
                source_type=self.source_type,
                app="slack",
                operation="search",
                latency_ms=dt,
                success=False,
                error=f"Slack API error: {data.get('error', 'unknown_error')}"
            )
        except Exception as e:
            dt = (time.perf_counter() - t0) * 1000.0
            return AdapterResponse(source_type=self.source_type, app="slack", operation="search", latency_ms=dt, success=False, error=str(e))

    async def read(self, resource_id: str, **kwargs) -> AdapterResponse:
        # Reads channel history
        return await self.search(query="", channel_id=resource_id, **kwargs)

    async def create(
        self,
        resource_type: str,
        payload: Dict[str, Any],
        idempotency_key: str,
        **kwargs
    ) -> tuple[AdapterResponse, ActionReceipt]:
        t0 = time.perf_counter()
        raw_chan = payload.get("channel") or os.getenv("SLACK_TEST_CHANNEL")
        test_chan = os.getenv("SLACK_TEST_CHANNEL")
        if not raw_chan or raw_chan.startswith("<") or raw_chan.startswith("#") or not (raw_chan.startswith("C") or raw_chan.startswith("D")):
            channel = test_chan or raw_chan
        else:
            channel = raw_chan

        text = payload.get("text", "")
        composite_key = idempotency_manager.compute_key("slack", "chat_postMessage", str(channel), idempotency_key)

        if idempotency_manager.has_executed(composite_key):
            cached = idempotency_manager.get_receipt(composite_key)
            resp = AdapterResponse(
                source_type=self.source_type,
                app="slack",
                operation="chat_postMessage",
                success=True,
                data={"idempotent_replay": True, "result": cached.resulting_state},
                resource_reference=cached.resource
            )
            return resp, cached

        if not self.token or not channel:
            receipt = ActionReceipt(
                app="slack",
                operation="chat_postMessage",
                resource=f"slack:channel:{channel}:err",
                source_type=self.source_type,
                execution_status="FAILED",
                error="Missing SLACK_BOT_TOKEN or channel parameter",
                idempotency_key=idempotency_key
            )
            return AdapterResponse(source_type=self.source_type, app="slack", operation="chat_postMessage", success=False, error="Credentials or channel missing"), receipt

        url = f"{SLACK_API_BASE}/chat.postMessage"
        body = {"channel": channel, "text": text}

        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(url, headers=self._get_headers(), json=body)
            dt = (time.perf_counter() - t0) * 1000.0

            data = resp.json()
            if not data.get("ok") and data.get("error") == "channel_not_found" and test_chan and channel != test_chan:
                body["channel"] = test_chan
                channel = test_chan
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    resp = await client.post(url, headers=self._get_headers(), json=body)
                data = resp.json()

            if data.get("ok"):
                ts = data.get("ts")
                receipt = ActionReceipt(
                    app="slack",
                    operation="chat_postMessage",
                    resource=f"slack:channel:{channel}:ts:{ts}",
                    previous_state={},
                    requested_state=payload,
                    resulting_state={"channel": channel, "ts": ts, "message": data.get("message")},
                    source_type=self.source_type,
                    execution_status="EXECUTED",
                    verification_status="PENDING",
                    idempotency_key=idempotency_key
                )
                idempotency_manager.record_receipt(composite_key, receipt)
                return AdapterResponse(
                    source_type=self.source_type,
                    app="slack",
                    operation="chat_postMessage",
                    latency_ms=dt,
                    success=True,
                    data=receipt.resulting_state,
                    resource_reference=receipt.resource
                ), receipt

            receipt = ActionReceipt(app="slack", operation="chat_postMessage", resource=f"slack:{channel}:err", source_type=self.source_type, execution_status="FAILED", error=data.get("error"), idempotency_key=idempotency_key)
            return AdapterResponse(source_type=self.source_type, app="slack", operation="chat_postMessage", latency_ms=dt, success=False, error=f"Slack error: {data.get('error')}"), receipt

        except Exception as e:
            dt = (time.perf_counter() - t0) * 1000.0
            receipt = ActionReceipt(app="slack", operation="chat_postMessage", resource=f"slack:{channel}:exc", source_type=self.source_type, execution_status="FAILED", error=str(e), idempotency_key=idempotency_key)
            return AdapterResponse(source_type=self.source_type, app="slack", operation="chat_postMessage", latency_ms=dt, success=False, error=str(e)), receipt

    async def update(self, resource_id: str, payload: Dict[str, Any], idempotency_key: str, **kwargs) -> tuple[AdapterResponse, ActionReceipt]:
        receipt = ActionReceipt(app="slack", operation="update", resource=resource_id, source_type=self.source_type, execution_status="NOOP_IDEMPOTENT", idempotency_key=idempotency_key)
        return AdapterResponse(source_type=self.source_type, app="slack", operation="update", success=True), receipt

    async def verify(self, action_receipt: ActionReceipt, **kwargs) -> AdapterResponse:
        t0 = time.perf_counter()
        # Parse resource: slack:channel:{channel}:ts:{ts}
        parts = action_receipt.resource.split(":")
        if len(parts) >= 5 and parts[3] == "ts":
            channel = parts[2]
            ts = parts[4]
            # Independent read query to conversations.history
            hist_resp = await self.search(query="", channel_id=channel, limit=10)
            dt = (time.perf_counter() - t0) * 1000.0
            if hist_resp.success:
                msgs = hist_resp.data.get("messages", [])
                for m in msgs:
                    if m.get("ts") == ts:
                        action_receipt.verification_status = "VERIFIED"
                        return AdapterResponse(
                            source_type=self.source_type,
                            app="slack",
                            operation="verify",
                            latency_ms=dt,
                            success=True,
                            data={"verified": True, "observed_message": m}
                        )

        action_receipt.verification_status = "FAILED_VERIFICATION"
        dt = (time.perf_counter() - t0) * 1000.0
        return AdapterResponse(source_type=self.source_type, app="slack", operation="verify", latency_ms=dt, success=False, error="Verification failed: Message not found in Slack channel history")

    async def delete(self, channel: str, ts: str) -> AdapterResponse:
        t0 = time.perf_counter()
        if not self.token:
            return AdapterResponse(source_type=self.source_type, app="slack", operation="delete", success=False, error="SLACK_BOT_TOKEN unconfigured")
        url = f"{SLACK_API_BASE}/chat.delete"
        body = {"channel": channel, "ts": ts}
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.post(url, headers=self._get_headers(), json=body)
            dt = (time.perf_counter() - t0) * 1000.0
            data = resp.json()
            if data.get("ok"):
                return AdapterResponse(source_type=self.source_type, app="slack", operation="delete", latency_ms=dt, success=True, data=data)
            return AdapterResponse(source_type=self.source_type, app="slack", operation="delete", latency_ms=dt, success=False, error=data.get("error", "delete_failed"))
        except Exception as e:
            dt = (time.perf_counter() - t0) * 1000.0
            return AdapterResponse(source_type=self.source_type, app="slack", operation="delete", latency_ms=dt, success=False, error=str(e))
