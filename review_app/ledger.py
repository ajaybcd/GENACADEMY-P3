"""Local, cross-session write guard. Contains run IDs and status, never credentials."""
import sqlite3
from pathlib import Path


class Ledger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS posts (run_id TEXT PRIMARY KEY, status TEXT NOT NULL, url TEXT)")

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def claim(self, run_id: str) -> bool:
        with self.connect() as db:
            cursor = db.execute("INSERT OR IGNORE INTO posts VALUES (?, 'attempted', NULL)", (run_id,))
            return cursor.rowcount == 1

    def get(self, run_id: str):
        with self.connect() as db:
            return db.execute("SELECT status, url FROM posts WHERE run_id=?", (run_id,)).fetchone()

    def complete(self, run_id: str, url: str):
        with self.connect() as db:
            db.execute("UPDATE posts SET status='posted', url=? WHERE run_id=?", (url, run_id))
