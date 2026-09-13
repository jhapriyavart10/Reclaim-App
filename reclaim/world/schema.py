"""
SQLite Schema for RECLAIM Local World State Engine.
Maintains persistent, queryable state across all 6 applications.
"""

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS crm_accounts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    arr REAL NOT NULL,
    health_score INTEGER NOT NULL,
    renewal_date TEXT NOT NULL,
    primary_contact_name TEXT NOT NULL,
    primary_contact_email TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS crm_notes (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    author TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(account_id) REFERENCES crm_accounts(id)
);

CREATE TABLE IF NOT EXISTS gmail_threads (
    id TEXT PRIMARY KEY,
    subject TEXT NOT NULL,
    snippet TEXT NOT NULL,
    last_message_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gmail_messages (
    id TEXT PRIMARY KEY,
    thread_id TEXT NOT NULL,
    from_address TEXT NOT NULL,
    to_address TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    is_draft INTEGER NOT NULL DEFAULT 0,
    is_sent INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY(thread_id) REFERENCES gmail_threads(id)
);

CREATE TABLE IF NOT EXISTS slack_channels (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    topic TEXT NOT NULL,
    is_private INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS slack_messages (
    id TEXT PRIMARY KEY,
    channel_name TEXT NOT NULL,
    user TEXT NOT NULL,
    text TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    thread_ts TEXT
);

CREATE TABLE IF NOT EXISTS github_repos (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    default_branch TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS github_pull_requests (
    id TEXT PRIMARY KEY,
    repo_name TEXT NOT NULL,
    number INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    author TEXT NOT NULL,
    state TEXT NOT NULL,
    merged_at TEXT,
    merge_commit_sha TEXT
);

CREATE TABLE IF NOT EXISTS github_commits (
    sha TEXT PRIMARY KEY,
    repo_name TEXT NOT NULL,
    author TEXT NOT NULL,
    message TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    diff_summary TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS github_issues (
    id TEXT PRIMARY KEY,
    repo_name TEXT NOT NULL,
    number INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    author TEXT NOT NULL,
    state TEXT NOT NULL,
    labels TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS github_comments (
    id TEXT PRIMARY KEY,
    issue_number INTEGER NOT NULL,
    repo_name TEXT NOT NULL,
    author TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jira_tickets (
    key TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    description TEXT NOT NULL,
    priority TEXT NOT NULL,
    status TEXT NOT NULL,
    assignee TEXT NOT NULL,
    reporter TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jira_comments (
    id TEXT PRIMARY KEY,
    ticket_key TEXT NOT NULL,
    author TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(ticket_key) REFERENCES jira_tickets(key)
);

CREATE TABLE IF NOT EXISTS calendar_events (
    id TEXT PRIMARY KEY,
    summary TEXT NOT NULL,
    description TEXT NOT NULL,
    start_time TEXT NOT NULL,
    end_time TEXT NOT NULL,
    attendees TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS action_audit_log (
    id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    app TEXT NOT NULL,
    operation TEXT NOT NULL,
    resource TEXT NOT NULL,
    previous_state TEXT NOT NULL,
    resulting_state TEXT NOT NULL,
    status TEXT NOT NULL,
    source_type TEXT NOT NULL,
    timestamp TEXT NOT NULL
);
"""
