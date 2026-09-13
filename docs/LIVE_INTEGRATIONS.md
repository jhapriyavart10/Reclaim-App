# RECLAIM Live Integrations Setup Guide

This document defines the exact operational requirements and security procedures for connecting RECLAIM's 3 primary live applications: **GitHub**, **Slack**, and **Jira**.

RECLAIM strictly adheres to the principle of least privilege. It will **never** perform writes against production resources by default and **refuses** to execute live mutation probes unless dedicated `TEST_*` resources are explicitly declared in the environment.

---

## 1. GitHub Integration

### 1. What Credential is Needed
A GitHub Personal Access Token (Classic or Fine-Grained PAT).

### 2. Where the Credential Comes From
Generate via GitHub Settings:
- GitHub -> **Settings** -> **Developer Settings** -> **Personal Access Tokens** -> **Tokens (classic)** (or Fine-grained tokens).
- Direct link: `https://github.com/settings/tokens`

### 3. Minimum Permissions / Scopes
- **Fine-Grained Token (Recommended)**:
  - Repository permissions:
    - **Issues**: Read and write
    - **Metadata**: Read-only
  - Target: Restrict only to your designated test repository (e.g. `your-user/reclaim-test`).
- **Classic Token**:
  - `repo` (Full control of private repositories) OR `public_repo` (if testing against a public test repo).

### 4. Environment Variable Names
```bash
GITHUB_TOKEN=ghp_yourPersonalAccessTokenHere
GITHUB_TEST_REPO=owner/reclaim-test-repo
```

### 5. Safe Test Procedure
The automated test follows a non-destructive lifecycle:
```
PRECONDITION -> WRITE -> INDEPENDENT READ -> ASSERT -> RESTORE -> INDEPENDENT READ -> PASS
```
1. Creates a temporary issue tagged `[RECLAIM TEST ONLY]`.
2. Reads back the issue independently from the GitHub REST API.
3. Closes the issue (`state: closed`).
4. Re-reads the issue to verify closed state.

### 6. Which Test Resources Should Be Used
A dedicated sandbox repository, such as `your-org/reclaim-sandbox` or `your-handle/reclaim-test`. **Never point `GITHUB_TEST_REPO` to production codebases.**

### 7. How to Reset / Revoke the Credentials
1. Visit `https://github.com/settings/tokens`.
2. Locate the token by name (e.g. `RECLAIM-Live-Probe`).
3. Click **Delete** or **Revoke**.

### 8. How the Connector Proves it is Live
- Queries `/repos/{repo}/issues` via HTTPS with Bearer authorization.
- Measures live HTTP roundtrip latency.
- Validates authentic GitHub HTTP response headers (`X-GitHub-Request-Id`, `ETag`).

### 9. How to Run the Tests
- **Read-Only Connectivity Smoke Test**:
  ```bash
  python -m reclaim.live_smoke
  ```
- **Safe Write Lifecycle Test**:
  ```bash
  python -m reclaim.live_test.github
  ```

---

## 2. Slack Integration

### 1. What Credential is Needed
A Slack Bot User OAuth Token (`xoxb-...`).

### 2. Where the Credential Comes From
Create an app in the Slack API Portal:
- Direct link: `https://api.slack.com/apps`
- Click **Create New App** -> **From scratch**.
- Navigate to **OAuth & Permissions**.
- Install the App to your workspace.

### 3. Minimum Permissions / Scopes
Under **Bot Token Scopes**, add ONLY:
- `channels:history` (Read messages in public channels the bot is in)
- `groups:history` (Read messages in private channels the bot is in)
- `chat:write` (Send messages)
- `chat:write.public` (Optional: post in public channels without joining)

### 4. Environment Variable Names
```bash
SLACK_BOT_TOKEN=xoxb-yourBotUserOAuthToken
SLACK_TEST_CHANNEL=reclaim-testing
```

### 5. Safe Test Procedure
```
PRECONDITION -> WRITE -> INDEPENDENT READ -> ASSERT -> RESTORE -> INDEPENDENT READ -> PASS
```
1. Checks that bot has access to `#reclaim-testing`.
2. Posts a temporary message `[RECLAIM TEST ONLY] Automated Verification Probe`.
3. Calls `conversations.history` to independently confirm the message arrived.
4. Calls `chat.delete` with the message timestamp (`ts`) to remove it.
5. Verifies message is absent from the channel.

### 6. Which Test Resources Should Be Used
A private or isolated public test channel (e.g. `#reclaim-bot-testing`). Ensure the bot user is invited to this channel (`/invite @ReclaimBot`).

### 7. How to Reset / Revoke the Credentials
1. Go to `https://api.slack.com/apps/{app_id}/oauth`.
2. Click **Revoke Tokens** or simply uninstall the app under **Install App** -> **Uninstall from Workspace**.

### 8. How the Connector Proves it is Live
- Sends authenticated POST to `https://slack.com/api/conversations.history`.
- Validates Slack API JSON response envelope `{"ok": true, ...}`.
- Measures real network latency.

### 9. How to Run the Tests
- **Read-Only Connectivity Smoke Test**:
  ```bash
  python -m reclaim.live_smoke
  ```
- **Safe Write Lifecycle Test**:
  ```bash
  python -m reclaim.live_test.slack
  ```

---

## 3. Jira Integration

### 1. What Credential is Needed
Atlassian Cloud Base URL, User Email, and Atlassian API Token.

### 2. Where the Credential Comes From
- Base URL: `https://your-domain.atlassian.net`
- API Token: Generate at Atlassian Security:
  `https://id.atlassian.com/manage-profile/security/api-tokens`
- Click **Create API token**.

### 3. Minimum Permissions / Scopes
- Basic Auth using email and API token inherits permissions of the user account.
- **Recommended**: Use a dedicated service account or assign permissions restricted only to a sandbox project (e.g. `TEST` or `SANDBOX`).
- Project Permissions required:
  - **Browse Projects**
  - **Create Issues**
  - **Edit Issues**
  - **Delete Issues** (or **Close Issues**)

### 4. Environment Variable Names
```bash
JIRA_URL=https://your-domain.atlassian.net
JIRA_USER_EMAIL=dev@your-company.com
JIRA_API_TOKEN=yourAtlassianApiTokenHere
JIRA_TEST_PROJECT=TEST
# Optional specific test ticket to verify non-destructive update
JIRA_TEST_ISSUE=TEST-101
```

### 5. Safe Test Procedure
```
PRECONDITION -> WRITE -> INDEPENDENT READ -> ASSERT -> RESTORE -> INDEPENDENT READ -> PASS
```
- **Path A (with `JIRA_TEST_ISSUE`)**:
  1. Reads original summary of `TEST-101`.
  2. Updates summary with temporary probe tag `[RECLAIM_TEST]`.
  3. Reads ticket back independently and asserts tag is present.
  4. Restores original summary.
  5. Re-reads ticket to verify restoration.
- **Path B (with `JIRA_TEST_PROJECT`)**:
  1. Creates probe issue `[RECLAIM TEST ONLY]`.
  2. Reads issue back independently via `/rest/api/3/issue/{key}`.
  3. Deletes issue via `DELETE /rest/api/3/issue/{key}`.
  4. Confirms issue is no longer accessible.

### 6. Which Test Resources Should Be Used
A dedicated Jira project created specifically for bot testing (e.g. project key `REC` or `TEST`).

### 7. How to Reset / Revoke the Credentials
1. Navigate to `https://id.atlassian.com/manage-profile/security/api-tokens`.
2. Locate the token and click **Revoke**.

### 8. How the Connector Proves it is Live
- Sends Basic Auth request to `https://your-domain.atlassian.net/rest/api/3/search` or `/rest/api/3/myself`.
- Verifies HTTP 200 and Jira Cloud JSON payload structure.
- Measures real network roundtrip latency.

### 9. How to Run the Tests
- **Read-Only Connectivity Smoke Test**:
  ```bash
  python -m reclaim.live_smoke
  ```
- **Safe Write Lifecycle Test**:
  ```bash
  python -m reclaim.live_test.jira
  ```

---

## 4. Operational Invariants & Policies

| Policy | Behavior |
|---|---|
| `MISSION_DATA_POLICY=STRICT_LIVE` | All mandatory actions and evidence MUST use LIVE connectors. If credentials are missing or connectors report `AUTHENTICATION_REQUIRED`, the mission **refuses to run** and halts immediately. |
| `MISSION_DATA_POLICY=HYBRID` | Prefers live connectors. If credentials are missing, allows simulated twins to provide context while **explicitly flagging provenance** as `SIMULATED` in the audit report. |
| `MISSION_DATA_POLICY=SIMULATION` | Uses local deterministic world-state engine for offline testing and benchmarking. |
