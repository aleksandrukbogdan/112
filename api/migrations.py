"""Portable SQLite/PostgreSQL additive migrations. Never edit a released entry."""
MIGRATIONS = [(1, [
    "CREATE TABLE IF NOT EXISTS content_versions (hash TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL, created DOUBLE PRECISION)",
    "CREATE TABLE IF NOT EXISTS session_events (session_id TEXT NOT NULL, seq INTEGER NOT NULL, event_id TEXT NOT NULL UNIQUE, actor BIGINT, ts DOUBLE PRECISION, type TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(session_id,seq))",
    "CREATE TABLE IF NOT EXISTS completion_receipts (session_id TEXT PRIMARY KEY, user_id BIGINT NOT NULL, kind TEXT NOT NULL, response TEXT NOT NULL, created DOUBLE PRECISION)",
    "CREATE TABLE IF NOT EXISTS evidence_reviews (session_id TEXT NOT NULL, revision INTEGER NOT NULL, actor BIGINT NOT NULL, reason TEXT NOT NULL, data TEXT NOT NULL, created DOUBLE PRECISION, PRIMARY KEY(session_id,revision))",
])]
