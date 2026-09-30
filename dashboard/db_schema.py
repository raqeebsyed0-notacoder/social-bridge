"""SQLite schema for social-bridge (additive only — never drops user data)."""
from __future__ import annotations

import sqlite3


def bootstrap(conn: sqlite3.Connection) -> None:
    """Create tables + seed defaults. Safe to call on every request."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS posts(
        id TEXT PRIMARY KEY, platform TEXT, author TEXT, author_url TEXT,
        text TEXT, url TEXT UNIQUE, ts TEXT, hash TEXT, read INT DEFAULT 0)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS watchlist(
        id TEXT PRIMARY KEY, platform TEXT, query TEXT, created_at INT)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY, value TEXT)"""
    )
    cur = conn.execute("SELECT value FROM settings WHERE key='poll_interval'")
    if not cur.fetchone():
        conn.execute(
            "INSERT INTO settings(key,value) VALUES('poll_interval','900')"
        )
        conn.execute(
            "INSERT INTO settings(key,value) VALUES("
            "'platforms_enabled','[\"x\",\"linkedin\",\"instagram\",\"youtube\"]')"
        )
        conn.commit()
