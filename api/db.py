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
import json
import os
import re
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

DATABASE_URL = os.environ.get("DATABASE_URL", "")
PG = DATABASE_URL.startswith("postgres")
DB_PATH = Path(os.environ.get("DB_PATH", "/app/state/t112.db"))
BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/app/state/backup"))
_lock = threading.RLock()

TABLES = ["groups", "users", "scenarios", "assignments", "sessions", "forecasts",
          "bkt", "audit", "settings"]
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
    return _conn


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
            if PG:
                c.commit()
            return r
        except Exception:
            if PG:
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
                rid = cur.fetchone()["id"]
            else:
                cur = c.execute(s, tuple(args))
                rid = getattr(cur, "lastrowid", None)
            c.commit()
            return rid
        except Exception:
            c.rollback()
            raise


@contextmanager
def tx():
    """Пакет изменений одной транзакцией."""
    with _lock:
        c = conn()

        class _T:
            def execute(self, sql, args=()):
                return c.execute(_sql(sql), tuple(args))
        try:
            yield _T()
            c.commit()
        except Exception:
            c.rollback()
            raise


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
    dump = {"format": "t112-backup-1", "created": time.time(), "backend": backend(),
            "tables": {t: q(f"SELECT * FROM {t}") for t in TABLES}}
    name = BACKUP_DIR / f"t112-{time.strftime('%Y%m%d-%H%M%S')}.json.gz"
    with gzip.open(name, "wt", encoding="utf-8") as f:
        json.dump(dump, f, ensure_ascii=False, default=str)
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
    return n


def list_backups() -> list[dict]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return [{"name": f.name, "size": f.stat().st_size, "ts": f.stat().st_mtime}
            for f in sorted(BACKUP_DIR.glob("t112-*.json.gz"), reverse=True)]


def start_backup_thread(period_sec: int = 86400) -> None:
    def loop():
        while True:
            time.sleep(period_sec)
            try:
                backup_now()
            except Exception:
                pass
    threading.Thread(target=loop, daemon=True).start()
