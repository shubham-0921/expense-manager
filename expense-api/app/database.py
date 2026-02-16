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
            name TEXT NOT NULL DEFAULT '',
            spreadsheet_id TEXT NOT NULL,
            sheet_name TEXT NOT NULL DEFAULT 'Sheet1',
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Migrate existing databases: add missing columns
    cursor = conn.execute("PRAGMA table_info(users)")
    columns = {row[1] for row in cursor.fetchall()}
    if "name" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN name TEXT NOT NULL DEFAULT ''")
    if "splitwise_token" not in columns:
        conn.execute("ALTER TABLE users ADD COLUMN splitwise_token TEXT DEFAULT ''")
    conn.commit()
    conn.close()


def register_user(telegram_user_id: str, spreadsheet_id: str, sheet_name: str = "Sheet1", name: str = ""):
    conn = get_connection()
    conn.execute(
        """INSERT INTO users (telegram_user_id, name, spreadsheet_id, sheet_name)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(telegram_user_id)
           DO UPDATE SET name = excluded.name,
                         spreadsheet_id = excluded.spreadsheet_id,
                         sheet_name = excluded.sheet_name""",
        (telegram_user_id, name, spreadsheet_id, sheet_name),
    )
    conn.commit()
    conn.close()


def set_splitwise_token(telegram_user_id: str, splitwise_token: str):
    conn = get_connection()
    conn.execute(
        "UPDATE users SET splitwise_token = ? WHERE telegram_user_id = ?",
        (splitwise_token, telegram_user_id),
    )
    conn.commit()
    conn.close()


def get_splitwise_token(telegram_user_id: str) -> str:
    conn = get_connection()
    row = conn.execute(
        "SELECT splitwise_token FROM users WHERE telegram_user_id = ?",
        (telegram_user_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return ""
    return row[0] or ""


def get_user(telegram_user_id: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE telegram_user_id = ?", (telegram_user_id,)
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return dict(row)
