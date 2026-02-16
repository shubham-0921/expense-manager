"""SQLite-based token storage for multi-user OAuth."""

import sqlite3
import uuid
import logging
from pathlib import Path
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "/data/tokens.db"


class TokenStore:
    """Stores user OAuth tokens in SQLite."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_token TEXT PRIMARY KEY,
                    splitwise_access_token TEXT NOT NULL,
                    splitwise_user_id INTEGER,
                    splitwise_name TEXT,
                    splitwise_email TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
        logger.info(f"TokenStore initialized at {self.db_path}")

    def create_user(self, splitwise_access_token: str, user_info: Optional[Dict[str, Any]] = None) -> str:
        """Store a new user's token. Returns the unique user_token (UUID)."""
        user_token = str(uuid.uuid4())
        user_info = user_info or {}
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO users (user_token, splitwise_access_token, splitwise_user_id, splitwise_name, splitwise_email) VALUES (?, ?, ?, ?, ?)",
                (
                    user_token,
                    splitwise_access_token,
                    user_info.get("id"),
                    user_info.get("name"),
                    user_info.get("email"),
                ),
            )
            conn.commit()
        logger.info(f"Created user token for {user_info.get('name', 'unknown')}")
        return user_token

    def get_access_token(self, user_token: str) -> Optional[str]:
        """Look up a Splitwise access token by user_token."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT splitwise_access_token FROM users WHERE user_token = ?",
                (user_token,),
            ).fetchone()
        return row[0] if row else None

    def delete_user(self, user_token: str) -> bool:
        """Remove a user's token."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("DELETE FROM users WHERE user_token = ?", (user_token,))
            conn.commit()
        return cursor.rowcount > 0
