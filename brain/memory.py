"""
v1 personalization: store the user's actual past messages per contact, and
retrieve the most recent few as few-shot examples when drafting a new one.

This is intentionally simple - no embeddings, no training. It's the
"20% effort, 80% of the value" version. See README roadmap for v2/v3.
"""
import sqlite3
import time
from typing import List

from config import settings


def _conn():
    conn = sqlite3.connect(settings.db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contact TEXT NOT NULL,
            text TEXT NOT NULL,
            timestamp REAL NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS preferences (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    return conn


def remember_message(contact: str, text: str) -> None:
    """Call this whenever a message the user actually sent (or approved
    after editing a draft) is known - that's the training signal."""
    conn = _conn()
    conn.execute(
        "INSERT INTO messages (contact, text, timestamp) VALUES (?, ?, ?)",
        (contact.lower().strip(), text, time.time()),
    )
    conn.commit()
    conn.close()


def get_style_examples(contact: str, limit: int = 5) -> List[str]:
    """Most recent messages to this contact, used as few-shot style
    reference. Falls back to the user's most recent messages to ANYONE
    if this specific contact has no history yet (cold start)."""
    conn = _conn()
    rows = conn.execute(
        "SELECT text FROM messages WHERE contact = ? ORDER BY timestamp DESC LIMIT ?",
        (contact.lower().strip(), limit),
    ).fetchall()

    if not rows:
        rows = conn.execute(
            "SELECT text FROM messages ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ).fetchall()

    conn.close()
    return [r[0] for r in rows]


def set_preference(key: str, value: str) -> None:
    conn = _conn()
    conn.execute(
        "INSERT INTO preferences (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_preference(key: str, default: str = "") -> str:
    conn = _conn()
    row = conn.execute("SELECT value FROM preferences WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row[0] if row else default
