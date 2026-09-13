import sqlite3
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from reclaim.world.schema import SCHEMA_SQL

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("reclaim_world.db")


class WorldStateEngine:
    """
    SQLite-backed local world state engine.
    Maintains persistent, queryable state for CRM, Gmail, Slack, GitHub, Jira, and Calendar.
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self.get_connection() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()

    def reset_database(self) -> None:
        """Completely drops all tables and re-initializes an empty world schema."""
        drop_tables_sql = """
        DROP TABLE IF EXISTS action_audit_log;
        DROP TABLE IF EXISTS calendar_events;
        DROP TABLE IF EXISTS jira_comments;
        DROP TABLE IF EXISTS jira_tickets;
        DROP TABLE IF EXISTS github_comments;
        DROP TABLE IF EXISTS github_issues;
        DROP TABLE IF EXISTS github_commits;
        DROP TABLE IF EXISTS github_pull_requests;
        DROP TABLE IF EXISTS github_repos;
        DROP TABLE IF EXISTS slack_messages;
        DROP TABLE IF EXISTS slack_channels;
        DROP TABLE IF EXISTS gmail_messages;
        DROP TABLE IF EXISTS gmail_threads;
        DROP TABLE IF EXISTS crm_notes;
        DROP TABLE IF EXISTS crm_accounts;
        """
        with self.get_connection() as conn:
            conn.executescript(drop_tables_sql)
            conn.executescript(SCHEMA_SQL)
            conn.commit()
        logger.info(f"Database {self.db_path} has been reset to an empty state.")

    # ------------------------------------------------------------------------
    # CRM Operations
    # ------------------------------------------------------------------------
    def get_crm_account(self, name_or_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM crm_accounts WHERE id = ? OR LOWER(name) LIKE LOWER(?)",
                (name_or_id, f"%{name_or_id}%")
            )
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_crm_account_health(self, account_id: str, health_score: int, status: Optional[str] = None) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute(
                    "UPDATE crm_accounts SET health_score = ?, status = ?, updated_at = datetime('now') WHERE id = ?",
                    (health_score, status, account_id)
                )
            else:
                cursor.execute(
                    "UPDATE crm_accounts SET health_score = ?, updated_at = datetime('now') WHERE id = ?",
                    (health_score, account_id)
                )
            conn.commit()
            return cursor.rowcount > 0

    # ------------------------------------------------------------------------
    # Gmail Operations
    # ------------------------------------------------------------------------
    def search_emails(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            like_query = f"%{query}%"
            cursor.execute(
                """
                SELECT * FROM gmail_messages
                WHERE subject LIKE ? OR body LIKE ? OR from_address LIKE ? OR to_address LIKE ?
                ORDER BY timestamp DESC LIMIT ?
                """,
                (like_query, like_query, like_query, like_query, limit)
            )
            return [dict(r) for r in cursor.fetchall()]

    def get_email_by_id(self, email_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM gmail_messages WHERE id = ?", (email_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def insert_email(
        self,
        email_id: str,
        thread_id: str,
        from_address: str,
        to_address: str,
        subject: str,
        body: str,
        is_draft: bool = False,
        is_sent: bool = True
    ) -> None:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO gmail_messages
                (id, thread_id, from_address, to_address, subject, body, timestamp, is_draft, is_sent)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'), ?, ?)
                """,
                (email_id, thread_id, from_address, to_address, subject, body, int(is_draft), int(is_sent))
            )
            conn.commit()

    # ------------------------------------------------------------------------
    # Slack Operations
    # ------------------------------------------------------------------------
    def search_slack_messages(self, query: str, channel_name: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            like_query = f"%{query}%"
            if channel_name:
                cursor.execute(
                    """
                    SELECT * FROM slack_messages
                    WHERE channel_name = ? AND (text LIKE ? OR user LIKE ?)
                    ORDER BY timestamp ASC LIMIT ?
                    """,
                    (channel_name, like_query, like_query, limit)
                )
            else:
                cursor.execute(
                    """
                    SELECT * FROM slack_messages
                    WHERE text LIKE ? OR user LIKE ?
                    ORDER BY timestamp ASC LIMIT ?
                    """,
                    (like_query, like_query, limit)
                )
            return [dict(r) for r in cursor.fetchall()]

    def insert_slack_message(self, msg_id: str, channel_name: str, user: str, text: str) -> None:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO slack_messages
                (id, channel_name, user, text, timestamp)
                VALUES (?, ?, ?, ?, datetime('now'))
                """,
                (msg_id, channel_name, user, text)
            )
            conn.commit()

    # ------------------------------------------------------------------------
    # GitHub Operations
    # ------------------------------------------------------------------------
    def search_github_prs(self, query: str) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            like_query = f"%{query}%"
            cursor.execute(
                """
                SELECT * FROM github_pull_requests
                WHERE title LIKE ? OR body LIKE ? OR author LIKE ?
                ORDER BY number DESC
                """,
                (like_query, like_query, like_query)
            )
            return [dict(r) for r in cursor.fetchall()]

    def get_github_pr(self, number: int) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM github_pull_requests WHERE number = ?", (number,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def search_github_commits(self, query: str) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            like_query = f"%{query}%"
            cursor.execute(
                """
                SELECT * FROM github_commits
                WHERE message LIKE ? OR diff_summary LIKE ?
                ORDER BY timestamp DESC
                """,
                (like_query, like_query)
            )
            return [dict(r) for r in cursor.fetchall()]

    def create_github_issue(self, issue_id: str, repo_name: str, number: int, title: str, body: str, author: str, labels: str = "") -> None:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO github_issues
                (id, repo_name, number, title, body, author, state, labels, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'open', ?, datetime('now'))
                """,
                (issue_id, repo_name, number, title, body, author, labels)
            )
            conn.commit()

    def get_github_issue(self, number: int) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM github_issues WHERE number = ?", (number,))
            row = cursor.fetchone()
            return dict(row) if row else None

    # ------------------------------------------------------------------------
    # Jira Operations
    # ------------------------------------------------------------------------
    def search_jira_tickets(self, query: str) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            like_query = f"%{query}%"
            cursor.execute(
                """
                SELECT * FROM jira_tickets
                WHERE key LIKE ? OR summary LIKE ? OR description LIKE ? OR assignee LIKE ?
                ORDER BY key ASC
                """,
                (like_query, like_query, like_query, like_query)
            )
            return [dict(r) for r in cursor.fetchall()]

    def get_jira_ticket(self, key: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM jira_tickets WHERE key = ?", (key,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def update_jira_ticket(self, key: str, updates: Dict[str, Any]) -> bool:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # Unpack nested Jira REST fields if provided by LLM
            flat_updates = dict(updates)
            if "fields" in flat_updates and isinstance(flat_updates["fields"], dict):
                for k, v in flat_updates["fields"].items():
                    flat_updates[k] = v

            valid_columns = {"summary", "description", "priority", "status", "assignee", "reporter"}
            fields = []
            values = []
            for k, v in flat_updates.items():
                if k in valid_columns and not isinstance(v, (dict, list)):
                    fields.append(f"{k} = ?")
                    values.append(str(v))

            if not fields:
                return True

            fields.append("updated_at = datetime('now')")
            values.append(key)
            sql = f"UPDATE jira_tickets SET {', '.join(fields)} WHERE key = ?"
            cursor.execute(sql, tuple(values))
            conn.commit()
            return cursor.rowcount > 0

    def create_jira_ticket(
        self,
        key: str,
        summary: str,
        description: str = "",
        priority: str = "Medium",
        status: str = "Open",
        assignee: str = "Unassigned",
        reporter: str = "agent"
    ) -> None:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO jira_tickets
                (key, summary, description, priority, status, assignee, reporter, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (key, summary, description, priority, status, assignee, reporter)
            )
            conn.commit()

    # ------------------------------------------------------------------------
    # Calendar Operations
    # ------------------------------------------------------------------------
    def search_calendar_events(self, query: str) -> List[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            like_query = f"%{query}%"
            cursor.execute(
                """
                SELECT * FROM calendar_events
                WHERE summary LIKE ? OR description LIKE ? OR attendees LIKE ?
                ORDER BY start_time ASC
                """,
                (like_query, like_query, like_query)
            )
            return [dict(r) for r in cursor.fetchall()]

    def get_calendar_event(self, event_id: str) -> Optional[Dict[str, Any]]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM calendar_events WHERE id = ?", (event_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def insert_calendar_event(self, event_id: str, summary: str, description: str, start_time: str, end_time: str, attendees: Any) -> None:
        if isinstance(attendees, (list, tuple)):
            attendees_str = ", ".join(str(a) for a in attendees)
        else:
            attendees_str = str(attendees or "")
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO calendar_events
                (id, summary, description, start_time, end_time, attendees, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 'confirmed', datetime('now'))
                """,
                (event_id, summary, description, start_time, end_time, attendees_str)
            )
            conn.commit()


# Default singleton instance
world_engine = WorldStateEngine()
