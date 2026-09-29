"""
Хранилище.

Основной режим — PostgreSQL 16 в отдельном контейнере (DATABASE_URL задан).
Без DATABASE_URL — SQLite-файл: для приёмочных тестов без Docker.

Код приложения пишет SQL с плейсхолдерами «?»; для PostgreSQL они
переводятся в «%s». INSERT в таблицы с первичным ключом id возвращает id.

Резервная копия — выгрузка всех таблиц в сжатый JSON. Так не нужен
pg_dump, версия которого обязана совпадать с версией сервера.
"""
from __future__ import annotations

import gzip
import logging
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

DATABASE_URL = os.environ.get("DATABASE_URL", "")
PG = DATABASE_URL.startswith("postgres")
DB_PATH = Path(os.environ.get("DB_PATH", "/app/state/t112.db"))
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/app/state/backup"))
_lock = threading.RLock()
_transaction = threading.local()
_backup_thread = None
_backup_thread_lock = threading.Lock()
_log = logging.getLogger(__name__)

TABLES = ["groups", "users", "scenarios", "assignments", "sessions", "forecasts",
          "bkt", "audit", "settings", "schema_migrations", "content_versions", "session_events",
          "completion_receipts", "evidence_reviews", "teacher_groups", "lessons", "lesson_members",
          "command_receipts", "session_snapshots", "helper_proposals", "materials",
          "scenario_revisions", "analytics_snapshots"]
WITH_ID = {"groups", "users", "scenarios", "assignments", "sessions", "forecasts", "audit"}


def _schema(pg: bool) -> str:
    serial = "BIGSERIAL PRIMARY KEY" if pg else "INTEGER PRIMARY KEY"
    real = "DOUBLE PRECISION" if pg else "REAL"
    return f"""
CREATE TABLE IF NOT EXISTS groups (
  id {serial}, name TEXT NOT NULL UNIQUE, created {real});
CREATE TABLE IF NOT EXISTS users (
  id {serial}, login TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('admin','teacher','trainee')),
  pw_hash TEXT NOT NULL, group_id BIGINT, active INTEGER NOT NULL DEFAULT 1, created {real});
CREATE TABLE IF NOT EXISTS scenarios (
  id TEXT PRIMARY KEY, source TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL,
  created_by BIGINT, validated_by BIGINT, created {real}, validated {real}, comment TEXT);
CREATE TABLE IF NOT EXISTS assignments (
  id {serial}, group_id BIGINT NOT NULL, scenario_id TEXT NOT NULL,
  rezhim TEXT NOT NULL DEFAULT 'ops112', dds_sluzhba TEXT,
  tayming_sec INTEGER, deadline TEXT, created_by BIGINT, created {real});
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY, user_id BIGINT NOT NULL, scenario_id TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'ops112',
  assignment_id BIGINT, started {real}, finished {real}, state TEXT NOT NULL,
  data TEXT NOT NULL, ball INTEGER, t_sec {real}, v_norm INTEGER, itog TEXT,
  override_ball INTEGER, override_by BIGINT, override_reason TEXT);
CREATE TABLE IF NOT EXISTS forecasts (
  id {serial}, kind TEXT NOT NULL, subject TEXT NOT NULL, created {real},
  data TEXT NOT NULL, fakt TEXT, fakt_at {real}, verno INTEGER);
CREATE TABLE IF NOT EXISTS bkt (
  user_id BIGINT, skill TEXT, p {real}, n INTEGER, updated {real},
  PRIMARY KEY (user_id, skill));
CREATE TABLE IF NOT EXISTS audit (
  id {serial}, ts {real}, user_id BIGINT, login TEXT, action TEXT, details TEXT);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS i_sess_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS i_sess_state ON sessions(state);
CREATE INDEX IF NOT EXISTS i_audit_ts ON audit(ts);
"""


_conn = None


def conn():
    with _lock:
        return _connect()

def _connect():
    global _conn
    if _conn is None:
        if PG:
            import psycopg
            from psycopg.rows import dict_row
            last = None
            for _ in range(60):                       # ждём, пока поднимется контейнер БД
                try:
                    _conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
                    break
                except Exception as e:
                    last = e
                    time.sleep(1)
            if _conn is None:
                raise RuntimeError(f"PostgreSQL недоступен: {last}")
            with _conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(112202609)")
                for st in _schema(True).split(";"):
                    if st.strip():
                        cur.execute(st)
            _conn.commit()
        else:
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            _conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.executescript(_schema(False))
            _conn.commit()
        migrate(_conn)
    return _conn


def migrate(c):
    """Additive migration: existing attempts and aggregate BKT rows are retained."""
    from .migrations import MIGRATIONS
    if PG:c.execute("SELECT pg_advisory_xact_lock(112202609)")
    c.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied DOUBLE PRECISION)")
    try:
        for version, statements in MIGRATIONS:
            if not c.execute(_sql("SELECT version FROM schema_migrations WHERE version=?"), (version,)).fetchone():
                for statement in statements:
                    c.execute(statement)
                c.execute(_sql("INSERT INTO schema_migrations(version,applied) VALUES(?,?)"), (version,time.time()))
        c.commit()
    except Exception:
        c.rollback()
        raise


def _sql(s: str) -> str:
    return s.replace("?", "%s") if PG else s


def _rows(cur) -> list[dict]:
    return [dict(r) for r in cur.fetchall()] if cur.description else []


def q(sql: str, args=()) -> list[dict]:
    with _lock:
        c = conn()
        try:
            cur = c.execute(_sql(sql), tuple(args))
            r = _rows(cur)
            if PG and not getattr(_transaction, "depth", 0):
                c.commit()
            return r
        except Exception:
            if PG and not getattr(_transaction, "depth", 0):
                c.rollback()
            raise


def q1(sql: str, args=()) -> dict | None:
    r = q(sql, args)
    return r[0] if r else None


def ex(sql: str, args=()):
    """Выполнить изменение. Для INSERT в таблицу с id вернуть id."""
    with _lock:
        c = conn()
        try:
            s = _sql(sql)
            m = re.match(r"\s*INSERT\s+INTO\s+(\w+)", sql, re.I)
            if PG and m and m.group(1) in WITH_ID and "RETURNING" not in sql.upper():
                s += " RETURNING id"
                cur = c.execute(s, tuple(args))
                returned = cur.fetchone()
                rid = returned["id"] if returned else None
            else:
                cur = c.execute(s, tuple(args))
                rid = getattr(cur, "lastrowid", None)
            if not getattr(_transaction, "depth", 0):
                c.commit()
            return rid
        except Exception:
            if not getattr(_transaction, "depth", 0):
                c.rollback()
            raise


@contextmanager
def tx():
    """Пакет изменений одной транзакцией."""
    with _lock:
        c = conn()
        depth = getattr(_transaction, "depth", 0)
        _transaction.depth = depth + 1

        class _T:
            def execute(self, sql, args=()):
                return c.execute(_sql(sql), tuple(args))
        try:
            if not depth and not PG:
                c.execute("BEGIN IMMEDIATE")
            yield _T()
            if not depth:
                c.commit()
        except Exception:
            if not depth:
                c.rollback()
            raise
        finally:
            _transaction.depth = depth


def setting(key: str, default=None):
    r = q1("SELECT value FROM settings WHERE key=?", (key,))
    return json.loads(r["value"]) if r else default


def set_setting(key: str, value) -> None:
    ex("INSERT INTO settings(key,value) VALUES(?,?) "
       "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
       (key, json.dumps(value, ensure_ascii=False)))


def audit(user: dict | None, action: str, details=None) -> None:
    ex("INSERT INTO audit(ts,user_id,login,action,details) VALUES(?,?,?,?,?)",
       (time.time(), user["id"] if user else None, user["login"] if user else "system",
        action, json.dumps(details or {}, ensure_ascii=False)))


def backend() -> str:
    return "PostgreSQL" if PG else "SQLite"


def size_bytes() -> int:
    if PG:
        r = q1("SELECT pg_database_size(current_database()) AS n")
        return int(r["n"]) if r else 0
    return DB_PATH.stat().st_size if DB_PATH.exists() else 0


# ---------------------------------------------------------------- резервные копии
# ТЗ: резервное копирование не реже 1 раза в сутки.

def backup_now(keep: int = 14) -> str:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    with tx() as txc:
        if PG:
            txc.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        dump = {"format": "t112-backup-2", "created": time.time(), "backend": backend(),
                "tables": {t: q(f"SELECT * FROM {t}") for t in TABLES}}
    name = BACKUP_DIR / f"t112-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.json.gz"
    temporary = name.with_suffix('.json.gz.tmp')
    try:
        with gzip.open(temporary, "wt", encoding="utf-8") as f:
            json.dump(dump, f, ensure_ascii=False, default=str)
        os.replace(temporary, name)
    finally:
        temporary.unlink(missing_ok=True)
    for old in sorted(BACKUP_DIR.glob("t112-*.json.gz"))[:-keep]:
        old.unlink(missing_ok=True)
    return str(name)


def restore(path: str) -> dict:
    """Восстановить все таблицы из резервной копии (текущие данные заменяются)."""
    with gzip.open(path, "rt", encoding="utf-8") as f:
        dump = json.load(f)
    n = {}
    with tx() as c:
        for t in reversed(TABLES):
            c.execute(f"DELETE FROM {t}")
        for t in TABLES:
            rows = dump["tables"].get(t, [])
            for r in rows:
                cols = list(r)
                c.execute(f"INSERT INTO {t}({','.join(cols)}) VALUES({','.join('?' * len(cols))})",
                          [r[k] for k in cols])
            n[t] = len(rows)
    if PG:   # счётчики id — после ручной вставки
        for t in WITH_ID - {"scenarios", "sessions"}:
            q(f"SELECT setval(pg_get_serial_sequence('{t}','id'), COALESCE((SELECT MAX(id) FROM {t}),0)+1, false)")
    migrate(conn())
    return n


def list_backups() -> list[dict]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return [{"name": f.name, "size": f.stat().st_size, "ts": f.stat().st_mtime}
            for f in sorted(BACKUP_DIR.glob("t112-*.json.gz"), reverse=True)]


def start_backup_thread(period_sec: int = 86400) -> None:
    """One scheduler per process; a cross-process lock elects the backup writer."""
    global _backup_thread
    if period_sec <= 0:
        raise ValueError('BACKUP_PERIOD_SEC must be positive')
    def loop():
        while True:
            time.sleep(period_sec)
            try:
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                if PG:
                    import psycopg
                    with psycopg.connect(DATABASE_URL, autocommit=True) as leader:
                        if not leader.execute('SELECT pg_try_advisory_lock(112202611)').fetchone()[0]:
                            continue
                        try:
                            _scheduled_backup()
                        finally:
                            leader.execute('SELECT pg_advisory_unlock(112202611)')
                else:
                    import fcntl
                    with (BACKUP_DIR / '.scheduler.lock').open('a+') as lock:
                        try:fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        except BlockingIOError:continue
                        _scheduled_backup()
            except Exception as exc:
                _log.exception('Scheduled backup failed')
                _backup_status('error',str(exc))
    with _backup_thread_lock:
        if _backup_thread is not None and _backup_thread.is_alive():return
        _backup_thread = threading.Thread(target=loop, name='t112-backup', daemon=True)
        _backup_thread.start()


def _backup_status(state: str, detail: str) -> None:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    status = BACKUP_DIR / 'scheduler-status.json'
    temporary = status.with_name(status.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps({'state':state,'at':time.time(),'detail':detail},ensure_ascii=False),encoding='utf-8')
        os.replace(temporary,status)
    finally:
        temporary.unlink(missing_ok=True)


def _scheduled_backup() -> None:
    # Other workers may wake together; a successful daily copy is sufficient.
    latest = sorted(BACKUP_DIR.glob('t112-*.json.gz'), key=lambda p:p.stat().st_mtime, reverse=True)
    if latest and time.time()-latest[0].stat().st_mtime < 20 * 3600:return
    _backup_status('running','')
    name = backup_now()
    _backup_status('ok',Path(name).name)


def lock_row(table, key, value):
    if table not in ("sessions", "lessons", "users", "groups") or key not in ("id",):
        raise ValueError("Unsupported row lock")
    if not getattr(_transaction, "depth", 0):
        raise RuntimeError("Row locks require a transaction")
    return q1(f"SELECT * FROM {table} WHERE {key}=?" + (" FOR UPDATE" if PG else ""), (value,))
