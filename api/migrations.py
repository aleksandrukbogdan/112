"""Portable SQLite/PostgreSQL additive migrations. Never edit a released entry."""
MIGRATIONS = [(1, [
    "CREATE TABLE IF NOT EXISTS content_versions (hash TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL, created DOUBLE PRECISION)",
    "CREATE TABLE IF NOT EXISTS session_events (session_id TEXT NOT NULL, seq INTEGER NOT NULL, event_id TEXT NOT NULL UNIQUE, actor BIGINT, ts DOUBLE PRECISION, type TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(session_id,seq))",
    "CREATE TABLE IF NOT EXISTS completion_receipts (session_id TEXT PRIMARY KEY, user_id BIGINT NOT NULL, kind TEXT NOT NULL, response TEXT NOT NULL, created DOUBLE PRECISION)",
    "CREATE TABLE IF NOT EXISTS evidence_reviews (session_id TEXT NOT NULL, revision INTEGER NOT NULL, actor BIGINT NOT NULL, reason TEXT NOT NULL, data TEXT NOT NULL, created DOUBLE PRECISION, PRIMARY KEY(session_id,revision))",
])]

MIGRATIONS.append((2, [
    "CREATE TABLE IF NOT EXISTS teacher_groups (teacher_id BIGINT NOT NULL, group_id BIGINT NOT NULL, PRIMARY KEY(teacher_id,group_id))",
    "CREATE TABLE IF NOT EXISTS lessons (id TEXT PRIMARY KEY, owner_id BIGINT NOT NULL, group_id BIGINT NOT NULL, state TEXT NOT NULL, created DOUBLE PRECISION, started DOUBLE PRECISION, stopped DOUBLE PRECISION, config TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS lesson_members (lesson_id TEXT NOT NULL, user_id BIGINT NOT NULL, data TEXT NOT NULL, PRIMARY KEY(lesson_id,user_id))",
    "CREATE TABLE IF NOT EXISTS command_receipts (user_id BIGINT NOT NULL, key TEXT NOT NULL, path TEXT NOT NULL, body_hash TEXT NOT NULL, response TEXT, status INTEGER, created DOUBLE PRECISION, PRIMARY KEY(user_id,key))",
    "CREATE TABLE IF NOT EXISTS session_snapshots (session_id TEXT NOT NULL, seq INTEGER NOT NULL, ts DOUBLE PRECISION NOT NULL, data TEXT NOT NULL, PRIMARY KEY(session_id,seq))",
    "CREATE TABLE IF NOT EXISTS helper_proposals (id TEXT PRIMARY KEY, session_id TEXT NOT NULL, field TEXT NOT NULL, base_version INTEGER NOT NULL, data TEXT NOT NULL, state TEXT NOT NULL, created DOUBLE PRECISION, decision TEXT, reviewed TEXT)",
    "CREATE TABLE IF NOT EXISTS materials (id TEXT PRIMARY KEY, owner_id BIGINT NOT NULL, title TEXT NOT NULL, filename TEXT NOT NULL, hash TEXT NOT NULL, content TEXT NOT NULL, meta TEXT NOT NULL, created DOUBLE PRECISION)",
    "CREATE TABLE IF NOT EXISTS scenario_revisions (scenario_id TEXT NOT NULL, revision INTEGER NOT NULL, actor BIGINT, data TEXT NOT NULL, comment TEXT, created DOUBLE PRECISION, PRIMARY KEY(scenario_id,revision))",
    "CREATE TABLE IF NOT EXISTS analytics_snapshots (id TEXT PRIMARY KEY, owner_id BIGINT NOT NULL, data TEXT NOT NULL, created DOUBLE PRECISION)",
    "CREATE INDEX IF NOT EXISTS i_lesson_state ON lessons(state)",
    "CREATE INDEX IF NOT EXISTS i_helper_session ON helper_proposals(session_id)",
    "CREATE INDEX IF NOT EXISTS i_session_snapshots ON session_snapshots(session_id,seq)",
]))
