"""Simple persistent SQLite store, sessions represented by SHA-256 hashes only."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_FILE = Path(os.getenv("DATABASE_PATH", "/appdata/sunupay.sqlite3"))


@contextmanager
def connection():
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_FILE), timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=10000")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def initialize():
    with connection() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              name TEXT NOT NULL,
              email TEXT NOT NULL UNIQUE,
              password_hash TEXT NOT NULL,
              merchant_wallet TEXT NOT NULL UNIQUE,
              customer_wallet TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS sessions (
              hash TEXT PRIMARY KEY,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              expires_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS invoices (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
              public_token TEXT NOT NULL UNIQUE,
              title TEXT NOT NULL,
              amount_sats INTEGER NOT NULL,
              address TEXT NOT NULL UNIQUE,
              lab_txid TEXT,
              created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS invoices_by_user ON invoices(user_id, id DESC);
            CREATE INDEX IF NOT EXISTS sessions_by_expiration ON sessions(expires_at);
        """)

        # Safe incremental migration: existing merchant accounts and Bitcoin Core
        # volumes remain intact when updating from the previous release.
        schema = {r[1] for r in db.execute("PRAGMA table_info(invoices)")}
        for name, ddl in {
            "method": "TEXT NOT NULL DEFAULT 'onchain'",
            "amount_xof": "INTEGER",
            "rate_btc_eur": "TEXT",
            "rate_at": "INTEGER",
            "lightning_checking_id": "TEXT",
            "lightning_invoice": "TEXT",
            "expires_at": "INTEGER",
            "paid_at": "TEXT",
        }.items():
            if name not in schema:
                db.execute(f"ALTER TABLE invoices ADD COLUMN {name} {ddl}")
        users = {r[1] for r in db.execute("PRAGMA table_info(users)")}
        for name, ddl in {
            "business_name": "TEXT NOT NULL DEFAULT ''",
            "city": "TEXT NOT NULL DEFAULT 'Dakar'",
            "language": "TEXT NOT NULL DEFAULT 'fr'",
        }.items():
            if name not in users:
                db.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
