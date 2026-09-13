import asyncio
import time
from typing import Dict, Any, Optional
import httpx

from reclaim.config import settings, sanitize_secret
from reclaim.llm.schemas import ProbeSchema
from reclaim.llm.gemini import GeminiProvider
from reclaim.llm.groq import GroqProvider, to_strict_json_schema, classify_rate_limit
from reclaim.llm.exceptions import LLMError


async def diagnose_groq_model(api_key: str, model_id: str, is_fallback: bool = False) -> Dict[str, Any]:
    """Diagnoses a specific Groq model with connectivity, strict structured output, and rate limit headers."""
    masked_key = sanitize_secret(api_key)
    res: Dict[str, Any] = {
        "model": model_id,
        "role": "FALLBACK" if is_fallback else "PRIMARY",
        "availability": "UNKNOWN",
        "connectivity": "UNKNOWN",
        "strict_structured_output": "UNKNOWN",
        "rate_limit_status": "UNKNOWN",
        "latency_ms": "N/A",
        "headers": {},
        "verdict": "UNKNOWN",
        "details": ""
    }

    strict_probe_schema = to_strict_json_schema(ProbeSchema.model_json_schema())
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers=headers,
                json={
                    "model": model_id,
                    "messages": [{"role": "user", "content": "Respond with status ok, provider_name groq, and latency_probe_id probe-groq-diag."}],
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "ProbeSchema",
                            "schema": strict_probe_schema,
                            "strict": True
                        }
                    }
                }
            )
        dt = (time.perf_counter() - t0) * 1000.0
        res["latency_ms"] = f"{dt:.1f}ms"

        # Capture rate limit headers
        hdrs_lower = {k.lower(): v for k, v in resp.headers.items()}
        res["headers"] = {
            "retry-after": hdrs_lower.get("retry-after", "none"),
            "rpm_limit": hdrs_lower.get("x-ratelimit-limit-requests", "unknown"),
            "rpm_remaining": hdrs_lower.get("x-ratelimit-remaining-requests", "unknown"),
            "tpm_limit": hdrs_lower.get("x-ratelimit-limit-tokens", "unknown"),
            "tpm_remaining": hdrs_lower.get("x-ratelimit-remaining-tokens", "unknown"),
            "reset_tokens": hdrs_lower.get("x-ratelimit-reset-tokens", "unknown")
        }

        if resp.status_code == 200:
            res["connectivity"] = "PASS"
            try:
                body = resp.json()["choices"][0]["message"]["content"]
                parsed = ProbeSchema.model_validate_json(body)
                if parsed.status in ("ok", "ready"):
                    res["strict_structured_output"] = "PASS"
                    res["verdict"] = "PASS"
                    res["rate_limit_status"] = f"Healthy (TPM Rem: {res['headers']['tpm_remaining']}/{res['headers']['tpm_limit']})"
                else:
                    res["strict_structured_output"] = "WARN"
                    res["verdict"] = "WARN"
                    res["details"] = f"Unexpected probe status: {parsed.status}"
            except Exception as parse_err:
                res["strict_structured_output"] = "FAIL"
                res["verdict"] = "FAIL"
                res["details"] = f"JSON parse error: {parse_err}"
        elif resp.status_code == 429:
            err_json = resp.json().get("error", {})
            rl_class = classify_rate_limit(429, resp.headers, err_json)
            res["connectivity"] = "PASS (HTTP 429 received)"
            res["strict_structured_output"] = "SKIPPED (Rate limited)"
            res["rate_limit_status"] = f"RATE_LIMITED ({rl_class})"
            clean_err = resp.text.replace(api_key, masked_key)
            res["details"] = clean_err[:160]
            # If fallback is active, a rate-limited primary is a WARN, but an unusable fallback is FAIL
            res["verdict"] = "WARN" if not is_fallback else "FAIL"
        else:
            res["connectivity"] = "FAIL"
            res["strict_structured_output"] = "FAIL"
            res["verdict"] = "FAIL"
            clean_err = resp.text.replace(api_key, masked_key)
            res["details"] = f"HTTP {resp.status_code}: {clean_err[:120]}"

    except Exception as exc:
        res["connectivity"] = "FAIL"
        res["strict_structured_output"] = "FAIL"
        res["verdict"] = "FAIL"
        res["details"] = str(exc).replace(api_key, masked_key)

    return res


async def run_doctor() -> None:
    print("==================================================")
    print("      RECLAIM LLM LAYER HEALTH DIAGNOSTIC        ")
    print("==================================================")
    print()

    groq_key = settings.get_effective_groq_key()
    primary_model = settings.groq_model
    fallback_model = settings.groq_fallback_model

    print(f"Configured Primary Model : {primary_model}")
    print(f"Configured Fallback Model: {fallback_model}")
    print(f"Credential Status        : {'PRESENT' if groq_key else 'MISSING'}")
    print()

    if not groq_key:
        print("[FAIL] GROQ_API_KEY is not configured in .env or environment.")
        return

    # Check model list endpoint first
    print("--- 1. Groq Model Registry Verification ---")
    headers = {"Authorization": f"Bearer {groq_key}"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            models_resp = await client.get("https://api.groq.com/openai/v1/models", headers=headers)
        if models_resp.status_code == 200:
            available_ids = {m["id"] for m in models_resp.json().get("data", [])}
            prim_avail = "PASS" if primary_model in available_ids else "FAIL"
            fall_avail = "PASS" if fallback_model in available_ids else "FAIL"
            print(f"[{prim_avail}] Primary Model '{primary_model}' in registry: {prim_avail}")
            print(f"[{fall_avail}] Fallback Model '{fallback_model}' in registry: {fall_avail}")
        else:
            print(f"[WARN] Failed to list models from Groq API: HTTP {models_resp.status_code}")
    except Exception as e:
        print(f"[WARN] Unable to verify models via /models endpoint: {e}")

    print()
    print("--- 2. Primary Model Probe (Strict Structured Outputs) ---")
    diag_primary = await diagnose_groq_model(groq_key, primary_model, is_fallback=False)
    print(f"Verdict                 : [{diag_primary['verdict']}]")
    print(f"Model                   : {diag_primary['model']}")
    print(f"Connectivity            : {diag_primary['connectivity']}")
    print(f"Strict Structured Output: {diag_primary['strict_structured_output']}")
    print(f"Estimated Latency       : {diag_primary['latency_ms']}")
    print(f"Rate Limit Signal       : {diag_primary['rate_limit_status']}")
    if diag_primary["details"]:
        print(f"Details                 : {diag_primary['details']}")

    print()
    print("--- 3. Fallback Model Probe (Strict Structured Outputs) ---")
    diag_fallback = await diagnose_groq_model(groq_key, fallback_model, is_fallback=True)
    print(f"Verdict                 : [{diag_fallback['verdict']}]")
    print(f"Model                   : {diag_fallback['model']}")
    print(f"Connectivity            : {diag_fallback['connectivity']}")
    print(f"Strict Structured Output: {diag_fallback['strict_structured_output']}")
    print(f"Estimated Latency       : {diag_fallback['latency_ms']}")
    print(f"Rate Limit Signal       : {diag_fallback['rate_limit_status']}")
    if diag_fallback["details"]:
        print(f"Details                 : {diag_fallback['details']}")

    print()
    print("--------------------------------------------------")
    print("              DIAGNOSTIC SUMMARY                  ")
    print("--------------------------------------------------")
    if diag_primary["verdict"] == "PASS":
        print(f"Status: PASS (Primary {primary_model} is fully operational and healthy)")
    elif diag_fallback["verdict"] == "PASS":
        print(f"Status: WARN (Primary {primary_model} is rate-limited; Fallback {fallback_model} is ACTIVE & USABLE)")
        print(f"RECLAIM will automatically and safely fail over to {fallback_model} with explicit provenance.")
    else:
        print("Status: FAIL (Neither primary nor fallback models are responding to structured outputs)")
    print("--------------------------------------------------")


if __name__ == "__main__":
    asyncio.run(run_doctor())
