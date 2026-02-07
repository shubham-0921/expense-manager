import sqlite3
from pathlib import Path

from app.config import settings

DB_PATH = Path(settings.database_path)


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            telegram_user_id TEXT PRIMARY KEY,
            spreadsheet_id TEXT NOT NULL,
            sheet_name TEXT NOT NULL DEFAULT 'Sheet1',
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def register_user(telegram_user_id: str, spreadsheet_id: str, sheet_name: str = "Sheet1"):
    conn = get_connection()
    conn.execute(
        """INSERT INTO users (telegram_user_id, spreadsheet_id, sheet_name)
           VALUES (?, ?, ?)
           ON CONFLICT(telegram_user_id)
           DO UPDATE SET spreadsheet_id = excluded.spreadsheet_id,
                         sheet_name = excluded.sheet_name""",
        (telegram_user_id, spreadsheet_id, sheet_name),
    )
    conn.commit()
    conn.close()


def get_user(telegram_user_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE telegram_user_id = ?", (telegram_user_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)
