"""
Counterfactual Beta Corp Crisis Dataset & Seeder.
Creates an internally consistent, distinct cross-app crisis:
Database Connection Pool Exhaustion (DB-208, PR #901, Slack #dev-backend, marcus@betacorp.com).
Used to deterministically prove that RECLAIM does not hardcode Acme Corp resources (PROD-1042 / PR #882).
"""

from datetime import datetime, timedelta, timezone
from reclaim.world.engine import WorldStateEngine


def seed_beta_world(engine: WorldStateEngine = None) -> None:
    db = engine or WorldStateEngine()
    db.reset_database()

    now = datetime.now(timezone.utc)
    t_minus_48h = (now - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_minus_36h = (now - timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_minus_24h = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_minus_18h = (now - timedelta(hours=18)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_minus_8h = (now - timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
    renewal_date = (now + timedelta(days=21)).strftime("%Y-%m-%d")

    with db.get_connection() as conn:
        cursor = conn.cursor()

        # 1. CRM: Beta Corp Account Profile
        cursor.execute(
            """
            INSERT INTO crm_accounts
            (id, name, arr, health_score, renewal_date, primary_contact_name, primary_contact_email, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "crm_acc_beta_001",
                "Beta Corp",
                180000.0,
                41,
                renewal_date,
                "Marcus Vance",
                "marcus@betacorp.com",
                "AT_RISK",
                t_minus_48h,
                t_minus_8h
            )
        )

        cursor.execute(
            """
            INSERT INTO crm_notes (id, account_id, author, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "note_crm_beta_01",
                "crm_acc_beta_001",
                "Elena Gomez (Account Director)",
                "Marcus Vance escalation: Evening batch database processing times out after 30s. SLA breached 2 nights in a row.",
                t_minus_8h
            )
        )

        # 2. Gmail: Escalation Email from Marcus
        cursor.execute(
            """
            INSERT INTO gmail_threads (id, subject, snippet, last_message_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                "th_beta_01",
                "CRITICAL: Database timeouts crashing Beta Corp nightly reconciliation",
                "Our database queries are hitting 30s timeouts consistently...",
                t_minus_8h
            )
        )
        cursor.execute(
            """
            INSERT INTO gmail_messages (id, thread_id, from_address, to_address, subject, body, timestamp, is_draft, is_sent)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1)
            """,
            (
                "em_beta_01",
                "th_beta_01",
                "marcus@betacorp.com",
                "support@reclaim-platform.com",
                "CRITICAL: Database timeouts crashing Beta Corp nightly reconciliation",
                "Team,\n\nOur database queries are hitting 30s timeouts consistently during batch settlement. We cannot reconcile daily ledgers. If this database deadlock/timeout issue is not resolved by tomorrow, we are pausing our contract renewal.\n\nMarcus Vance | VP Infrastructure, Beta Corp",
                t_minus_8h
            )
        )

        # 3. Slack: #dev-backend & #incidents
        cursor.execute(
            """
            INSERT INTO slack_messages (id, channel_name, user, text, timestamp, thread_ts)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "msg_beta_01",
                "dev-backend",
                "alex-infra",
                "Alert: Database connection pool exhaustion under load. Pool max size reached; pending queries failing with timeout.",
                t_minus_24h,
                None
            )
        )
        cursor.execute(
            """
            INSERT INTO slack_messages (id, channel_name, user, text, timestamp, thread_ts)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "msg_beta_02",
                "incidents",
                "oncall-bot",
                ":warning: P1 Outage Incident: Beta Corp batch job database timeout exceptions on db-cluster-primary.",
                t_minus_18h,
                None
            )
        )
        # Decoy message
        cursor.execute(
            """
            INSERT INTO slack_messages (id, channel_name, user, text, timestamp, thread_ts)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "msg_beta_decoy",
                "random",
                "sam-designer",
                "Anyone want to grab tacos for lunch today?",
                t_minus_18h,
                None
            )
        )

        # 4. Jira: DB-208 (The actual culprit ticket) and decoy ticket
        cursor.execute(
            """
            INSERT INTO jira_tickets (key, summary, description, priority, status, assignee, reporter, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "DB-208",
                "Database connection pool exhaustion under burst transaction load",
                "Beta Corp and high-throughput batch consumers experiencing pool timeout exceptions after worker overflow limit decreased.",
                "High",
                "In Progress",
                "alex-infra",
                "elena-support",
                t_minus_24h,
                t_minus_18h
            )
        )
        cursor.execute(
            """
            INSERT INTO jira_tickets (key, summary, description, priority, status, assignee, reporter, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "DB-199",
                "Update local docker-compose Postgres seed scripts",
                "Routine developer tooling update for local environments.",
                "Low",
                "Closed",
                "dev-intern",
                "alex-infra",
                t_minus_48h,
                t_minus_36h
            )
        )

        # 5. GitHub: PR #901 (The actual root cause) and decoy PR
        cursor.execute(
            """
            INSERT INTO github_pull_requests (id, repo_name, number, title, body, author, state, merged_at, merge_commit_sha)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pr_beta_901",
                "core-platform",
                901,
                "Refactor DB connection pooling thresholds and max overflow limit",
                "Reduced max_overflow from 50 to 5 to save idle memory on container instances. May constrain burst batch jobs.",
                "maria-db",
                "merged",
                t_minus_24h,
                "sha_db_901_pool_overflow"
            )
        )
        cursor.execute(
            """
            INSERT INTO github_pull_requests (id, repo_name, number, title, body, author, state, merged_at, merge_commit_sha)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pr_beta_910",
                "core-platform",
                910,
                "Upgrade prettier dependency to 3.2.0",
                "Routine dependabot bump for code formatter.",
                "dependabot[bot]",
                "merged",
                t_minus_18h,
                "sha_tooling_910_prettier"
            )
        )

        conn.commit()


if __name__ == "__main__":
    eng = WorldStateEngine()
    seed_beta_world(eng)
    print("Beta Corp counterfactual world successfully seeded.")
