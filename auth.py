import sqlite3
import bcrypt
from pathlib import Path

DB_PATH = Path("users.db")

ROLE_PERMISSIONS = {
    "Engineer": ["Engineering", "General"],
    "HR": ["HR", "General"],
    "Manager": ["Engineering", "HR", "General"],
}


def setup_database(create_demo_users=False):
    conn = sqlite3.connect(DB_PATH)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL
        )
    """)

    if create_demo_users:
        demo_users = [
            ("engineer", "engineer123", "Engineer"),
            ("hr", "hr123", "HR"),
            ("manager", "manager123", "Manager"),
        ]

        for username, password, role in demo_users:
            password_hash = bcrypt.hashpw(
                password.encode(), bcrypt.gensalt()
            ).decode()

            conn.execute("""
                INSERT OR REPLACE INTO users
                (username, password_hash, role)
                VALUES (?, ?, ?)
            """, (username, password_hash, role))

    conn.commit()
    conn.close()


def authenticate_user(username, password):
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT username, password_hash, role FROM users WHERE username = ?",
        (username,),
    ).fetchone()
    conn.close()

    if not row:
        return None

    if not bcrypt.checkpw(
        password.encode(),
        row[1].encode()
    ):
        return None

    role = row[2]

    return {
        "username": row[0],
        "role": role,
        "allowed_departments": ROLE_PERMISSIONS.get(role, []),
    }
