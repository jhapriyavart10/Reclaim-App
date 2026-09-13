import asyncio
import json
import os
import time
from typing import Dict, Any, List
import numpy as np
import httpx
from dotenv import load_dotenv

from reclaim.config import settings
from reclaim.llm.schemas import GoalInterpretation, MissionDecision
from reclaim.llm.groq import to_strict_json_schema, classify_rate_limit

load_dotenv()
api_key = settings.get_effective_groq_key()

MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b"
]

BENCHMARK_PROMPTS = [
    {
        "name": "GoalInterpretation_Acme",
        "schema_cls": GoalInterpretation,
        "schema_name": "GoalInterpretation",
        "prompt": (
            "Analyze the following operational business rescue objective:\n"
            "\"Save Acme Corp: Resolve production 504 API outage, unblock customer sync, and preserve enterprise renewal.\"\n\n"
            "Decompose this into: target_entity, desired_outcome, success_conditions, constraints, "
            "initial_information_gaps, and candidate_app_domains (crm, gmail, slack, jira, github, calendar)."
        )
    },
    {
        "name": "MissionDecision_AcmeOutage",
        "schema_cls": MissionDecision,
        "schema_name": "MissionDecision",
        "prompt": (
            "ROOT CAUSE SYNTHESIS:\n"
            "Target: Acme Corp. Disruption: 504 Gateway Timeout on /v2/data-sync.\n"
            "Evidence 1 (github): PR #882 introduced strict schema validation breaking payload format.\n"
            "Evidence 2 (slack): #incident-war-room discussion confirming 100% sync drop for Acme.\n"
            "Evidence 3 (jira): Tracking issue SCRUM-5 open with Priority=Medium.\n\n"
            "Formulate prioritized remediation actions:\n"
            "1. Escalate Jira issue SCRUM-5 to High/Highest.\n"
            "2. Post status update to Slack channel #incident-war-room.\n"
            "Ensure risk_level and verification_method are accurately specified."
        )
    }
]


async def test_single_call(
    client: httpx.AsyncClient,
    model: str,
    test_case: Dict[str, Any]
) -> Dict[str, Any]:
    strict_schema = to_strict_json_schema(test_case["schema_cls"].model_json_schema())
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a precise, mission-critical autonomous enterprise AI agent. You MUST respond with a single valid JSON object strictly conforming to the requested schema."},
            {"role": "user", "content": test_case["prompt"]}
        ],
        "temperature": 0.1,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": test_case["schema_name"],
                "schema": strict_schema,
                "strict": True
            }
        }
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    t0 = time.perf_counter()
    status_code = 0
    err_msg = ""
    parsed_ok = False
    action_correct = False
    tokens = 0

    try:
        resp = await client.post("https://api.groq.com/openai/v1/chat/completions", json=body, headers=headers, timeout=25.0)
        dt = (time.perf_counter() - t0) * 1000.0
        status_code = resp.status_code

        if status_code == 200:
            data = resp.json()
            usage = data.get("usage", {})
            tokens = usage.get("total_tokens", 0)
            content = data["choices"][0]["message"]["content"]
            try:
                parsed_obj = test_case["schema_cls"].model_validate_json(content)
                parsed_ok = True
                if isinstance(parsed_obj, MissionDecision):
                    # Check if action selection contains Jira or Slack
                    apps = {a.app for a in parsed_obj.selected_actions}
                    action_correct = ("jira" in apps or "slack" in apps)
                elif isinstance(parsed_obj, GoalInterpretation):
                    action_correct = ("acme" in parsed_obj.target_entity.lower())
            except Exception as e:
                err_msg = f"Pydantic validation: {e}"
        else:
            err_json = resp.json().get("error", {})
            err_msg = err_json.get("message", resp.text)
    except Exception as e:
        dt = (time.perf_counter() - t0) * 1000.0
        status_code = 500
        err_msg = str(e)

    return {
        "model": model,
        "test": test_case["name"],
        "status_code": status_code,
        "latency_ms": dt,
        "tokens": tokens,
        "parsed_ok": parsed_ok,
        "action_correct": action_correct,
        "error": err_msg[:120] if err_msg else ""
    }


async def run_benchmark(samples_per_prompt: int = 2) -> None:
    print("==================================================")
    print("      RECLAIM GROQ MODEL BENCHMARK HARNESS        ")
    print(f"      Models: {', '.join(MODELS)}")
    print(f"      Samples: {samples_per_prompt} per prompt ({samples_per_prompt * len(BENCHMARK_PROMPTS)} total calls per model)")
    print("==================================================")
    print()

    results: Dict[str, List[Dict[str, Any]]] = {m: [] for m in MODELS}

    async with httpx.AsyncClient() as client:
        for model in MODELS:
            print(f"--- Benchmarking {model} ---")
            for tc in BENCHMARK_PROMPTS:
                for s in range(samples_per_prompt):
                    print(f"  [{model}] Running {tc['name']} (sample {s+1}/{samples_per_prompt})...", end="", flush=True)
                    res = await test_single_call(client, model, tc)
                    results[model].append(res)
                    print(f" Status: {res['status_code']}, Latency: {res['latency_ms']:.0f}ms, Structured: {res['parsed_ok']}")
                    # Sleep 3 seconds between calls to respect free tier TPM
                    await asyncio.sleep(3.0)
            print()

    print("==================================================")
    print("               BENCHMARK RESULTS                  ")
    print("==================================================")
    print(f"{'Metric':<32} | {'openai/gpt-oss-120b':<20} | {'openai/gpt-oss-20b':<20}")
    print("-" * 78)

    for metric_name, calculator in [
        ("Total Requests Attempted", lambda items: f"{len(items)}"),
        ("Request Success Rate (200 OK)", lambda items: f"{sum(1 for i in items if i['status_code'] == 200) / max(1, len(items)) * 100:.1f}%"),
        ("Structured Output Success Rate", lambda items: f"{sum(1 for i in items if i['parsed_ok']) / max(1, len(items)) * 100:.1f}%"),
        ("Validation Failure Rate", lambda items: f"{sum(1 for i in items if i['status_code'] == 400) / max(1, len(items)) * 100:.1f}%"),
        ("429 Rate-Limit Rate", lambda items: f"{sum(1 for i in items if i['status_code'] == 429) / max(1, len(items)) * 100:.1f}%"),
        ("Correct Action-Selection Rate", lambda items: f"{sum(1 for i in items if i['action_correct']) / max(1, sum(1 for i in items if i['parsed_ok'])) * 100 if any(i['parsed_ok'] for i in items) else 0:.1f}%"),
        ("Average Latency", lambda items: f"{np.mean([i['latency_ms'] for i in items if i['status_code'] == 200]):.1f} ms" if any(i['status_code'] == 200 for i in items) else "N/A"),
        ("P95 Latency", lambda items: f"{np.percentile([i['latency_ms'] for i in items if i['status_code'] == 200], 95):.1f} ms" if any(i['status_code'] == 200 for i in items) else "N/A"),
        ("Average Token Usage", lambda items: f"{np.mean([i['tokens'] for i in items if i['tokens'] > 0]):.0f}" if any(i['tokens'] > 0 for i in items) else "N/A"),
    ]:
        val_120 = calculator(results["openai/gpt-oss-120b"])
        val_20 = calculator(results["openai/gpt-oss-20b"])
        print(f"{metric_name:<32} | {val_120:<20} | {val_20:<20}")

    print("==================================================")
    print()


if __name__ == "__main__":
    asyncio.run(run_benchmark(samples_per_prompt=2))
