"""
Red-Herring Variant of Acme Corp Crisis Dataset & Seeder.
Creates an adversarial environment where:
1. Newest PR (#899) is an unrelated frontend hero layout update.
2. The real cause is an older commit (PR #882 / serializer regression).
3. Slack contains misleading red-herring speculation ("Maybe AWS is having a DNS issue in us-east-1?").
4. Jira contains two similar tickets (PROD-1042 data-sync bug vs PROD-1020 catering invoice / PROD-1035 UI typography).
"""

from datetime import datetime, timedelta, timezone
from reclaim.world.engine import WorldStateEngine


def seed_red_herring_world(engine: WorldStateEngine = None) -> None:
    db = engine or WorldStateEngine()
    db.reset_database()

    now = datetime.now(timezone.utc)
    t_minus_48h = (now - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_minus_36h = (now - timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_minus_24h = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_minus_12h = (now - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_minus_2h = (now - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    renewal_date = (now + timedelta(days=14)).strftime("%Y-%m-%d")

    with db.get_connection() as conn:
        cursor = conn.cursor()

        # 1. CRM
        cursor.execute(
            """
            INSERT INTO crm_accounts
            (id, name, arr, health_score, renewal_date, primary_contact_name, primary_contact_email, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "crm_acc_acme_001",
                "Acme Corp",
                250000.0,
                34,
                renewal_date,
                "Sarah Chen",
                "sarah@acme.com",
                "AT_RISK",
                t_minus_48h,
                t_minus_12h
            )
        )

        cursor.execute(
            """
            INSERT INTO crm_notes (id, account_id, author, content, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "note_crm_01",
                "crm_acc_acme_001",
                "Dave Miller (Enterprise AE)",
                "Sarah Chen escalated: 500 errors on /v2/data-sync. Threatening churn if not fixed immediately.",
                t_minus_12h
            )
        )

        # 2. Gmail
        cursor.execute(
            """
            INSERT INTO gmail_threads (id, subject, snippet, last_message_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                "th_acme_01",
                "URGENT: Acme Corp data sync failure on /v2/data-sync",
                "Our sync jobs are failing with HTTP 500 errors continuously...",
                t_minus_12h
            )
        )
        cursor.execute(
            """
            INSERT INTO gmail_messages (id, thread_id, from_address, to_address, subject, body, timestamp, is_draft, is_sent)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1)
            """,
            (
                "em_acme_01",
                "th_acme_01",
                "sarah@acme.com",
                "support@reclaim-platform.com",
                "URGENT: Acme Corp data sync failure on /v2/data-sync",
                "Our sync jobs are failing with HTTP 500 errors continuously. We cannot access customer analytics. Fix this or we cancel our $250k contract.",
                t_minus_12h
            )
        )

        # 3. Slack: Genuine alerts + Red-Herring Misdirection
        cursor.execute(
            """
            INSERT INTO slack_messages (id, channel_name, user, text, timestamp, thread_ts)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "msg_acme_real",
                "alerts-enterprise",
                "api-gateway-bot",
                "CRITICAL: 500 Internal Server Error spike on /v2/data-sync for tenant acme-prod.",
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
                "msg_acme_misdirection",
                "alerts-enterprise",
                "junior-dev-leo",
                "Hey guys, maybe AWS us-east-1 is having an undocumented DNS routing failure causing those 500s?",
                t_minus_12h,
                None
            )
        )
        cursor.execute(
            """
            INSERT INTO slack_messages (id, channel_name, user, text, timestamp, thread_ts)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "msg_acme_lunch",
                "random",
                "alice",
                "Should we order Thai food or Mediterranean for the all-hands lunch tomorrow?",
                t_minus_12h,
                None
            )
        )

        # 4. Jira: Two similar tickets (PROD-1042 real bug vs PROD-1020 catering invoice)
        cursor.execute(
            """
            INSERT INTO jira_tickets (key, summary, description, priority, status, assignee, reporter, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "PROD-1042",
                "Tenant acme-prod receiving 500 Internal Server Error on /v2/data-sync",
                "API v2 endpoint failing with serialization error when tenant submits legacy null fields.",
                "Medium",
                "Open",
                "unassigned",
                "api-gateway-bot",
                t_minus_24h,
                t_minus_12h
            )
        )
        cursor.execute(
            """
            INSERT INTO jira_tickets (key, summary, description, priority, status, assignee, reporter, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "PROD-1020",
                "Quarterly office catering and snack reorder invoice #994",
                "Invoice reconciliation for office snack vendor and espresso beans.",
                "Low",
                "Closed",
                "office-manager",
                "finance-bot",
                t_minus_48h,
                t_minus_36h
            )
        )

        # 5. GitHub: Newest PR #899 is a red herring; older PR #882 is the real culprit!
        cursor.execute(
            """
            INSERT INTO github_pull_requests (id, repo_name, number, title, body, author, state, merged_at, merge_commit_sha)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pr_redherring_899",
                "core-platform",
                899,
                "Update hero marketing section typography and CSS button margin",
                "Decoy PR: Non-functional styling changes to public marketing site.",
                "ui-designer-sam",
                "merged",
                t_minus_2h,
                "sha_ui_899_css"
            )
        )
        cursor.execute(
            """
            INSERT INTO github_pull_requests (id, repo_name, number, title, body, author, state, merged_at, merge_commit_sha)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pr_real_882",
                "core-platform",
                882,
                "Upgrade API response serializer to strict validation schema",
                "Real Root Cause: Enforces non-null payload constraints on /v2/ endpoints without fallback.",
                "tech_lead_alex",
                "merged",
                t_minus_24h,
                "sha_api_882_serializer"
            )
        )

        conn.commit()


if __name__ == "__main__":
    eng = WorldStateEngine()
    seed_red_herring_world(eng)
    print("Red-Herring Acme world successfully seeded.")
