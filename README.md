# RECLAIM — Autonomous Business Rescue Agent

> **"Give an AI agent an outcome. Watch it investigate across real tools, reason, decide, act safely, and independently verify recovery."**

---

## 🎬 2-Minute Demo Video
> 🏆 **HACKATHON SUBMISSION NOTICE**: Per the hackathon requirements, this single repository contains all required code and documentation. Judges can access both the project and the demonstration video directly below:
> 
> 📺 **Watch the 2-Minute Demo Video**: **[Click Here to Watch the Demo Video](https://www.youtube.com/watch?v=_nUaU67X1vs)** *(Replace with your unlisted YouTube or Loom link)*

---

## 👥 Users & Target Audience

In modern enterprise operations, production outages and customer churn crises scatter evidence across fragmented silos: code changes in **GitHub**, alerts and discussions in **Slack**, tracking tickets in **Jira**, customer health in **CRM**, and urgent email threads in **Gmail**. 

Current incident response forces humans to manually context-switch between 6+ tabs to correlate clues, while brittle workflow automation scripts break whenever parameters shift.

**RECLAIM is built for:**
1. **Incident Commanders & SREs**: Who need an autonomous agent to correlate alerts, commits, and tickets in seconds during P0 outages.
2. **Technical Account Managers & Enterprise Support**: Who must de-escalate customer crises with technical accuracy before SLAs breach.
3. **Operations & Engineering Leaders**: Who demand complete auditability, guaranteed independent write verification, and human authorization gates on high-risk operations.

---

## ⚡ What Makes RECLAIM a Genuine Agent?

RECLAIM is **not a chatbot** and **not a hardcoded workflow script**. It is an autonomous agent operating under strict control theory:

| Feature | Scripted / Chatbot Approach | RECLAIM Autonomous Agent |
| :--- | :--- | :--- |
| **Investigation** | Static, hardcoded sequence | Dynamic capability-grounded search across apps based on evolving evidence |
| **Reasoning** | Regex matching or unconstrained chat | Structured cross-app synthesis requiring corroboration across ≥ 2 distinct systems |
| **Action Selection** | Hardcoded conditional branches | Autonomous hypothesis evaluation generating prioritized remediation proposals |
| **Integrity & Provenance** | Silent fallback to hardcoded actions | **Non-negotiable Decision Integrity**: explicit `reasoning_status` (`LLM_REASONING`, `FAILED`) and `decision_source` (`groq:openai/...`). No fabricated decisions. |
| **Safety & Control** | Blind writes or manual copy-paste | Two-tier Safety Gate: autonomous internal execution vs. cryptographically signed token approvals for customer writes |
| **Verification** | Assumed success on HTTP 200 | **Independent Post-State Verification Engine**: queries target system post-execution to mathematically prove state change |

---

## 🔌 Connected Applications & Architecture

RECLAIM features **3 Real Live External Integrations** and high-fidelity enterprise twin simulations:

- 🐙 **GitHub (LIVE)**: Inspects live repository branches, PR diffs, and commit histories via GitHub REST API.
- 💬 **Slack (LIVE)**: Interrogates live incident channels (`#incident-war-room`, `#alerts-enterprise`), reads messages, and posts verified updates via Slack WebClient.
- 📋 **Jira (LIVE)**: Discovers live issues, transitions issue priorities, and posts comment audits via Jira Cloud REST API.
- 📊 **CRM (High-Fidelity Twin)**: Tracks account revenue risk, ARR health scores, and customer SLA tiers.
- 📧 **Gmail & 📅 Calendar (High-Fidelity Twins)**: Surfaces customer escalation emails and schedules emergency triage meetings.

```
                         +-----------------------------+
                         |      High-Level Goal        |
                         |   "Save Acme Corp: P0"      |
                         +--------------+--------------+
                                        |
                                        v
                         +-----------------------------+
                         |    Goal Interpretation      |
                         |     (Groq GPT-OSS-120B)     |
                         +--------------+--------------+
                                        |
                                        v
                         +-----------------------------+
                         | Dynamic Planner & Discovery |
                         |   (Adapter Registry Check)  |
                         +--------------+--------------+
                                        |
                 +----------------------+----------------------+
                 |                      |                      |
                 v                      v                      v
        [ GitHub Live API ]    [ Slack Live WebClient ] [ Jira Cloud API ]
                 |                      |                      |
                 +----------------------+----------------------+
                                        |
                                        v
                         +-----------------------------+
                         |   Evidence Store & Digest   |
                         |  (Compressed Factual Graph) |
                         +--------------+--------------+
                                        |
                                        v
                         +-----------------------------+
                         | Multi-App Root Cause Engine |
                         |    (Cross-App Corroboration)|
                         +--------------+--------------+
                                        |
                                        v
                         +-----------------------------+
                         |  Decision Engine & Safety   |
                         |  - Low-Risk: Auto Execute   |
                         |  - High-Risk: Human Approval|
                         +--------------+--------------+
                                        |
                                        v
                         +-----------------------------+
                         | Independent Verification    |
                         | (Query Target Post-State)   |
                         +-----------------------------+
```

---

## 🚀 How to Run RECLAIM

### 1. Prerequisites
- **Python**: 3.11 or 3.12+ (tested on Python 3.13)
- **Node.js**: 18+ (tested on Node v20+)
- **Groq API Key**: Get a free API key at [console.groq.com](https://console.groq.com/keys)

### 2. Configuration (`.env`)
Create or edit `.env` in the repository root:
```ini
# LLM Configuration (Groq)
GROQ_API_KEY=gsk_your_groq_api_key_here
GROQ_MODEL=openai/gpt-oss-120b
GROQ_FALLBACK_MODEL=openai/gpt-oss-20b
LLM_PROVIDER=groq

# Optional Live Credentials (if testing live integrations):
GITHUB_TOKEN=ghp_your_github_token
SLACK_BOT_TOKEN=xoxb_your_slack_token
JIRA_SERVER=https://your-domain.atlassian.net
JIRA_EMAIL=your-email@domain.com
JIRA_API_TOKEN=your_jira_token
```

### 3. Launch Backend API Server
In your first terminal (root directory):
```powershell
python -m reclaim.api.server
```
*Backend runs on `http://localhost:5000` with REST APIs, Server-Sent Events (SSE) telemetry, and human-in-the-loop approval endpoints.*

### 4. Launch Mission Control Frontend
In your second terminal:
```powershell
cd frontend
npm install
npm run dev
```
*Vite dev server starts on `http://localhost:5173` with instant hot-reloading and proxy to port 5000.*

### 5. Access Mission Control UI
Open your browser to **[http://localhost:5173](http://localhost:5173)**:
- Click **"Investigate & Resolve Incident"** to watch the real-time agent lifecycle.
- Review multi-app evidence cards, causal hypothesis DAG, action receipt log, and 100% verified state proofs.
- Approve or reject high-risk actions in the interactive Human-in-the-Loop authorization modal.

---

## 🛡️ How We Test Reliability

RECLAIM is engineered for rigorous, auditable production reliability.

### 1. Full Automated Regression Suite (97 passed, 5 skipped, 0 failed)
Run the complete regression suite:
```powershell
python -m pytest tests/ -v
```
All **93+ automated tests** pass clean, verifying:
- Goal decomposition and capability-aware planning
- Tool order invariance (agent draws the same conclusion regardless of query order)
- Counterfactual adaptation (different enterprise world states yield different remediation actions)
- Red-herring discrimination (ignoring decoy alerts that do not corroborate)
- Safety Gate enforcement (mandatory token authorization for customer-facing writes)
- Verification failure recovery (agent detects unverified mutations and transitions to recovery)
- Idempotency preservation (replaying identical actions produces cached execution receipts)

### 2. LLM Layer Hardening & Reliability Tests
Run the dedicated LLM reliability suite:
```powershell
python -m pytest tests/test_llm_reliability.py -v
```
Verifies:
- **Evidence-based 429 Classification**: Automatically classifies `RATE_LIMIT_TOKEN` (TPM), `RATE_LIMIT_DAILY_TOKEN` (TPD), `RATE_LIMIT_REQUEST` (RPM), and `RATE_LIMIT_DAILY_REQUEST` (RPD).
- **Bounded Exponential Backoff**: Honors `retry-after` header; immediately fails over to fallback model if wait exceeds 15s.
- **Request Deduplication & Budget Enforcement**: In-memory hash caching prevents identical LLM queries; per-run request/token caps prevent budget overrun.
- **Strict Structured Outputs**: Validates schemas using `to_strict_json_schema()` with `additionalProperties: false` and strict property definitions.
- **Hostile Decision Integrity Tests**: Mathematically proves that when LLM calls fail, RECLAIM refuses to silently invent deterministic business decisions, surfacing an explicit `FAILED` state.

### 3. Live LLM Diagnostic Doctor
Run the live system health doctor:
```powershell
python -m reclaim.llm.doctor
```
Checks:
- Live connectivity against Groq `/openai/v1/models`
- Primary model (`openai/gpt-oss-120b`) availability
- Fallback model (`openai/gpt-oss-20b`) availability and usability
- Rate limit headers (TPM/RPM limits, remaining tokens, reset countdowns)
- Strict JSON Schema compatibility

> *Doctor Status Note*: The doctor reports PASS when models have available token quota. If running on a free-tier API key whose daily limit (200k tokens) has been exhausted by prior test runs, the doctor truthfully reports FAIL/WARN with the exact rate-limit reset countdown.

### 4. Empirical Model Benchmark Harness
Run the head-to-head Groq benchmark:
```powershell
python benchmark_groq.py
```
Empirically measures and compares `openai/gpt-oss-120b` vs `openai/gpt-oss-20b` across:
- Structured output success rate
- Validation failure rate
- 429 rate
- Average and P95 latency
- Token consumption
- Action selection accuracy

---

## 📊 Reliability Scorecard & Live Run Traces

### A. Historical Successful Execution (`RECLAIM-LIVE-5dfdb03f`)
- **Immutable Trace**: [`artifacts/live_runs/RECLAIM-LIVE-5dfdb03f/trace.json`](artifacts/live_runs/RECLAIM-LIVE-5dfdb03f/trace.json)
- **Status**: `COMPLETED` (`reasoning_status: LLM_REASONING`, `decision_source: groq:openai/gpt-oss-120b`)
- **Measured During**: End-to-end live execution across live GitHub, Slack, and Jira:

| Metric | Measured Value | Standard Required | Status |
| :--- | :--- | :--- | :--- |
| **Verification Rate** | **100.0%** (2/2 live writes verified) | ≥ 90.0% | **PASS** |
| **Unverified Mutations** | **0** | 0 | **PASS** |
| **Substantive Deterministic Fallback**| **0.0%** (Enforced by state machine & hostile tests) | 0.0% | **PASS** |
| **Evidence Corroboration** | **≥ 2 distinct apps** (32 live items) | ≥ 2 apps | **PASS** |
| **Automated Test Results** | **97 passed, 5 skipped, 0 failed** (environment-dependent live connector skips) | 0 failures | **PASS** |
| **Composite Agenticity Score** | **0.95 / 1.00** *(Internal Agenticity Heuristic)* | ≥ 0.80 | **PASS (GENUINE_AGENT)** |

> *Note on Composite Agenticity Score*: Evaluated via `reclaim.evaluation.scorecard.AgenticityScorecard`, an internal heuristic rubric measuring cross-app corroboration ($\ge 2$ apps), tool-order invariance, 100% read-back verification, and safety-gate risk compliance. It is not an external industry benchmark.

### B. Latest Provider-Blocked Execution (`RECLAIM-LIVE-20260913202721-7d1b97`)

- **Latest Pointer**: [`artifacts/latest_live_trace.json`](artifacts/latest_live_trace.json) — dynamically validated pointer to the newest live execution.

- **Immutable Trace**: [`artifacts/live_runs/RECLAIM-LIVE-20260913202721-7d1b97/trace.json`](artifacts/live_runs/RECLAIM-LIVE-20260913202721-7d1b97/trace.json)

- **Outcome**: `final_status: "FAILED"`, `reasoning_status: "FAILED"`, `decision_source: "none"`, `executed_actions: []`.

- **Root Cause**: Groq free-tier daily token quota exhaustion (`RATE_LIMIT_DAILY_TOKEN`, HTTP 429; limit `200,000` tokens/day, with approximately `199,206` tokens used and `5,659` requested during the blocked attempt).

- **Proved Invariant**: When substantive LLM reasoning is unavailable, RECLAIM refuses to silently invent a deterministic business decision and halts safely with zero mutations.

- **Dynamic Quota Transition**: *Provider quota availability is dynamic; the health probe and mission are separate API interactions and may observe different remaining quota.*

- **Machine-Checkable Invariant**: Enforced by `reclaim.evaluation.scorecard.validate_live_golden_path_invariant()`. A run is certified as a successful Golden Path only when all 8 conditions are satisfied: HTTP 200 reasoning response, structured validation pass, `reasoning_status=LLM_REASONING`, `decision_source=groq:*`, non-empty actions, live mutation executed, 100% read-back verification, and final state `COMPLETED`.

---

## 📂 Repository Structure

```
├── reclaim/
│   ├── adapters/            # Multi-app adapters (Live & High-Fidelity Simulated)
│   │   ├── live/            # GitHub (REST), Slack (WebClient), Jira (Cloud REST)
│   │   ├── simulated/       # CRM, Gmail, Calendar, Jira, Slack twins
│   │   ├── base.py          # Adapter contracts, SourceType, RiskLevel
│   │   └── registry.py      # Runtime dynamic capability discovery
│   ├── core/                # Autonomous Agent Core Subsystems
│   │   ├── orchestrator.py  # FSM lifecycle, telemetry, deterministic report
│   │   ├── decision_engine.py # Decision formulation with Decision Integrity
│   │   ├── evidence_store.py# Cross-app entity normalization & evidence graph
│   │   ├── goal_parser.py   # High-level objective interpretation
│   │   ├── planner.py       # Capability-grounded planning & factual digest
│   │   ├── safety_gate.py   # Two-tier risk classifier & token approval
│   │   └── state_machine.py # Formal Finite State Machine controller
│   ├── llm/                 # Hardened LLM Provider Subsystem
│   │   ├── groq.py          # Groq provider: strict schema, 429 backoff, dedup
│   │   ├── schemas.py       # Strict Pydantic models with decision provenance
│   │   ├── base.py          # Provider ABC, UsageStats, LLMResponse
│   │   └── doctor.py        # Diagnostic doctor command
│   ├── api/                 # Flask backend server & SSE streaming
│   │   └── server.py        # REST API + Server-Sent Events telemetry
│   └── config.py            # Single source of truth settings
├── frontend/                # Mission Control SPA (React + TypeScript + Vite)
│   ├── src/                 # App.tsx, App.css, api.ts, types.ts
│   └── index.html           # Enterprise Mission Control layout
├── tests/                   # Full automated test suite (93+ tests)
│   ├── test_llm_reliability.py # 429 classification, backoff, decision integrity
│   ├── test_agent_core.py      # Core agent lifecycle & invariants
│   ├── test_real_agentic_behavior.py # Invariance, counterfactuals, adaptability
│   └── test_live_integrations.py    # GitHub, Slack, Jira live connectivity
├── benchmark_groq.py        # Empirical benchmark for Groq 120B vs 20B
├── .env.example             # Documented environment template
└── README.md                # Submission master documentation
```

---

## ⚖️ Hackathon Submission Verification Checklist
- [x] **No Chatbot / Free-Form Text**: Single-screen autonomous Mission Control dashboard.
- [x] **At Least 3 External Integrations**: Real GitHub, Slack, and Jira connections verified.
- [x] **Genuine Agentic Behavior**: Evidence-backed hypothesis formulation, tool order invariance, and counterfactual decision shifts.
- [x] **Truthful Provenance**: Real LLM reasoning (`groq:openai/gpt-oss-...`) with zero silent substantive fallback.
- [x] **Self-Contained Repository**: Everything runnable in this single repository with comprehensive instructions.
- [x] **2-Minute Demo Link**: Prominently featured at the top of this README.
