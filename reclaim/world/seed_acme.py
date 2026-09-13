"""
Canonical Acme Corp Churn Crisis Dataset & Seeder.
Creates an internally consistent, cross-app crisis with realistic temporal ordering and red herrings.
"""

from datetime import datetime, timedelta, timezone
from reclaim.world.engine import WorldStateEngine, DEFAULT_DB_PATH


def seed_acme_world(engine: WorldStateEngine = None) -> None:
    db = engine or WorldStateEngine()
    db.reset_database()

    now = datetime.now(timezone.utc)
    t_minus_72h = (now - timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_minus_48h = (now - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_minus_46h = (now - timedelta(hours=46)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_minus_44h = (now - timedelta(hours=44)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_minus_40h = (now - timedelta(hours=40)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_minus_36h = (now - timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_minus_30h = (now - timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_minus_14h = (now - timedelta(hours=14)).strftime("%Y-%m-%dT%H:%M:%SZ")
    t_minus_12h = (now - timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
    renewal_date = (now + timedelta(days=14)).strftime("%Y-%m-%d")

    with db.get_connection() as conn:
        cursor = conn.cursor()

        # -------------------------------------------------------------
        # 1. CRM: Account Profile & Health Trajectory
        # -------------------------------------------------------------
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
                34,  # Dropped from 92 -> 34
                renewal_date,
                "Sarah Chen",
                "sarah@acme.com",
                "AT_RISK",
                t_minus_72h,
                t_minus_14h
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
                "Renewal negotiation underway for $250,000 tier. Sarah Chen is primary champion. Health score plummeted today after reported sync outage.",
                t_minus_14h
            )
        )

        # -------------------------------------------------------------
        # 2. Gmail: Executive Churn Threat & Red Herring Thread
        # -------------------------------------------------------------
        # Real Critical Thread:
        cursor.execute(
            """
            INSERT INTO gmail_threads (id, subject, snippet, last_message_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                "th_gmail_acme_urgent",
                "CRITICAL: Production Data Sync Broken / Renewal Cancellation Notice",
                "Our production pipeline has been down for 36 hours since your update...",
                t_minus_12h
            )
        )

        cursor.execute(
            """
            INSERT INTO gmail_messages
            (id, thread_id, from_address, to_address, subject, body, timestamp, is_draft, is_sent)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1)
            """,
            (
                "msg_gmail_sarah_threat",
                "th_gmail_acme_urgent",
                "sarah@acme.com",
                "dave@ourcompany.com",
                "CRITICAL: Production Data Sync Broken / Renewal Cancellation Notice",
                (
                    "Dave,\n\n"
                    "I am writing this because our engineering team has reached a breaking point. "
                    "Ever since your Thursday deployment, our automated ETL on `/v2/data-sync` has been throwing continuous 500 errors. "
                    "This has halted our customer analytics for over 36 hours with zero status communication from your team.\n\n"
                    "Our annual contract renewal of $250,000 is due in two weeks. "
                    "If this production regression is not addressed today with a concrete fix and an executive post-mortem, "
                    "we are terminating our contract and cancelling the renewal.\n\n"
                    "Sarah Chen\n"
                    "Chief Technology Officer | Acme Corp"
                ),
                t_minus_12h
            )
        )

        # Red Herring Thread in Gmail (Old resolved billing question from 2 months ago):
        t_old_resolved = (now - timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
        cursor.execute(
            """
            INSERT INTO gmail_threads (id, subject, snippet, last_message_at)
            VALUES (?, ?, ?, ?)
            """,
            ("th_gmail_billing_old", "Question on Q2 Invoice Typo", "Thanks for fixing the billing address...", t_old_resolved)
        )
        cursor.execute(
            """
            INSERT INTO gmail_messages (id, thread_id, from_address, to_address, subject, body, timestamp, is_draft, is_sent)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 1)
            """,
            (
                "msg_gmail_billing_old",
                "th_gmail_billing_old",
                "sarah@acme.com",
                "billing@ourcompany.com",
                "Question on Q2 Invoice Typo",
                "Thank you for promptly issuing the corrected invoice. All settled on our end.",
                t_old_resolved
            )
        )

        # -------------------------------------------------------------
        # 3. Slack: Technical Chat, Escalation & Red Herring
        # -------------------------------------------------------------
        cursor.execute("INSERT INTO slack_channels (id, name, topic) VALUES ('c1', 'alerts-enterprise', 'Automated Production Alerts')")
        cursor.execute("INSERT INTO slack_channels (id, name, topic) VALUES ('c2', 'customer-acme', 'Dedicated Customer Channel for Acme Corp')")
        cursor.execute("INSERT INTO slack_channels (id, name, topic) VALUES ('c3', 'general', 'General company banter')")

        # Alerts channel
        cursor.execute(
            """
            INSERT INTO slack_messages (id, channel_name, user, text, timestamp)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "slk_m1",
                "alerts-enterprise",
                "datadog-bot",
                ":warning: High 500 error rate on `/v2/data-sync` endpoint for tenant `acme-prod` (status: 500 Internal Server Error, rate: 120 req/min).",
                t_minus_40h
            )
        )

        # Customer triage channel
        cursor.execute(
            """
            INSERT INTO slack_messages (id, channel_name, user, text, timestamp)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "slk_m2",
                "customer-acme",
                "rachel.tam",
                "Hey @eng-oncall, Acme Corp mentioned their sync jobs are failing with 500 errors. Anyone know what changed?",
                t_minus_36h
            )
        )
        cursor.execute(
            """
            INSERT INTO slack_messages (id, channel_name, user, text, timestamp)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "slk_m3",
                "customer-acme",
                "bob.engineer",
                "I filed a ticket PROD-1042 yesterday to look into it, but marked it medium priority since we were busy with the sprint demo.",
                t_minus_30h
            )
        )

        # Red Herring Slack message in #general:
        cursor.execute(
            """
            INSERT INTO slack_messages (id, channel_name, user, text, timestamp)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                "slk_m_red_herring",
                "general",
                "alex.designer",
                "Whoever ordered the cold vegan wraps for the team offsite catering yesterday needs to be banned from ordering food forever.",
                t_minus_14h
            )
        )

        # -------------------------------------------------------------
        # 4. GitHub: Breaking PR #882 & Red Herring PR
        # -------------------------------------------------------------
        cursor.execute("INSERT INTO github_repos (id, name, default_branch) VALUES ('repo_1', 'backend-core', 'main')")

        # The Culprit PR: PR #882
        cursor.execute(
            """
            INSERT INTO github_pull_requests
            (id, repo_name, number, title, body, author, state, merged_at, merge_commit_sha)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pr_882",
                "backend-core",
                882,
                "Enforce strict JSON schema serialization on API v2 payload handlers",
                "Migrates `/v2/data-sync` payload serializer to strict Pydantic v2 schemas. Disallows unexpected legacy metadata fields.",
                "dev-kevin",
                "merged",
                t_minus_48h,
                "c9a12b7f"
            )
        )

        cursor.execute(
            """
            INSERT INTO github_commits (sha, repo_name, author, message, timestamp, diff_summary)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                "c9a12b7f",
                "backend-core",
                "dev-kevin",
                "Enforce strict schema on /v2/data-sync payload serializer",
                t_minus_48h,
                "diff --git a/api/v2/serializer.py: - extra = 'allow' + extra = 'forbid' (causes SchemaValidationError on client payload containing legacy_id)"
            )
        )

        # Red Herring PR #870 (Unrelated docs change):
        cursor.execute(
            """
            INSERT INTO github_pull_requests (id, repo_name, number, title, body, author, state, merged_at, merge_commit_sha)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "pr_870",
                "backend-core",
                870,
                "Update README and contributing guidelines",
                "Typo fixes and markdown linting in repo docs.",
                "intern-sam",
                "merged",
                (now - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "f88301aa"
            )
        )

        # -------------------------------------------------------------
        # 5. Jira: Under-prioritized Bug PROD-1042 & Red Herring Bug
        # -------------------------------------------------------------
        # The Culprit Ticket (Under-prioritized in backlog):
        cursor.execute(
            """
            INSERT INTO jira_tickets
            (key, summary, description, priority, status, assignee, reporter, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "PROD-1042",
                "Intermittent 500 error on /v2/data-sync endpoint",
                "Acme Corp reported payload failures on /v2/data-sync following the v2.4.0 release. Trace shows ValidationError on unexpected field.",
                "Medium",
                "Backlog",
                "Unassigned",
                "bob.engineer",
                t_minus_30h,
                t_minus_30h
            )
        )

        # Red Herring Ticket PROD-1020 (Resolved UI bug from 3 weeks ago):
        cursor.execute(
            """
            INSERT INTO jira_tickets
            (key, summary, description, priority, status, assignee, reporter, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "PROD-1020",
                "Acme export button icon misalignment in Safari",
                "Minor CSS offset in the export dropdown when viewed in older Safari versions.",
                "Low",
                "Closed",
                "frontend-dev",
                "rachel.tam",
                (now - timedelta(days=21)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                (now - timedelta(days=18)).strftime("%Y-%m-%dT%H:%M:%SZ")
            )
        )

        # -------------------------------------------------------------
        # 6. Calendar: Existing Events & Free Window for Executive Sync
        # -------------------------------------------------------------
        start_slot = (now + timedelta(hours=2)).strftime("%Y-%m-%dT14:00:00Z")
        end_slot = (now + timedelta(hours=2, minutes=30)).strftime("%Y-%m-%dT14:30:00Z")

        cursor.execute(
            """
            INSERT INTO calendar_events
            (id, summary, description, start_time, end_time, attendees, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "evt_team_standup",
                "Daily Engineering Standup",
                "Regular daily sync",
                (now - timedelta(hours=3)).strftime("%Y-%m-%dT10:00:00Z"),
                (now - timedelta(hours=2, minutes=30)).strftime("%Y-%m-%dT10:30:00Z"),
                "team@company.com",
                "confirmed",
                t_minus_72h
            )
        )

        conn.commit()

    print(f"Canonical Acme Corp world successfully seeded into {db.db_path}.")


if __name__ == "__main__":
    seed_acme_world()
