"""
Аутентификация и роли.

Пароли — PBKDF2-SHA256, 200 000 итераций. Токен — подписанный HMAC,
без внешних библиотек. Три роли по ТЗ: администратор, преподаватель,
обучающийся.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path

from fastapi import Depends, Header, HTTPException

from . import db

TOKEN_TTL = int(os.environ.get("TOKEN_TTL_SEC", 12 * 3600))


def _secret() -> bytes:
    s = os.environ.get("SECRET_KEY")
    if s:
        return s.encode()
    f = db.DB_PATH.parent / "secret.key"
    if not f.exists():
        f.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd=os.open(f,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as out:out.write(secrets.token_hex(32))
        except FileExistsError:pass
    return f.read_text().strip().encode()


def hash_pw(pw: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()
    return f"{salt}${h}"


def check_pw(pw: str, stored: str) -> bool:
    try:
        salt, h = stored.split("$", 1)
    except ValueError:
        return False
    x = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()
    return hmac.compare_digest(x, h)


def make_token(uid: int) -> str:
    exp = int(time.time()) + TOKEN_TTL
    msg = f"{uid}:{exp}"
    sig = hmac.new(_secret(), msg.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{msg}:{sig}".encode()).decode()


def read_token(tok: str) -> int | None:
    try:
        raw = base64.urlsafe_b64decode(tok.encode()).decode()
        uid, exp, sig = raw.split(":")
        msg = f"{uid}:{exp}"
        good = hmac.new(_secret(), msg.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, good) or int(exp) < time.time():
            return None
        return int(uid)
    except Exception:
        return None


def current_user(authorization: str = Header(default="")) -> dict:
    tok = authorization.removeprefix("Bearer ").strip()
    uid = read_token(tok) if tok else None
    if not uid:
        raise HTTPException(401, "требуется вход")
    u = db.q1("SELECT id,login,name,role,group_id,active FROM users WHERE id=?", (uid,))
    if not u or not u["active"]:
        raise HTTPException(401, "пользователь не найден или отключён")
    return u


def role(*roles: str):
    def dep(u: dict = Depends(current_user)) -> dict:
        if u["role"] not in roles:
            raise HTTPException(403, "недостаточно прав")
        return u
    return dep


def ensure_defaults() -> list[dict]:
    """
    Первый запуск: создать группу и три учётные записи.
    Пароли берутся из окружения; если не заданы — стандартные,
    и об этом пишется в журнал запуска.
    """
    if db.q1("SELECT id FROM users LIMIT 1"):
        return []
    gid = db.ex("INSERT INTO groups(name,created) VALUES(?,?)",
                ("Учебная группа 1", time.time()))
    made = []
    for login, name, rl, env in [
        ("admin", "Администратор", "admin", "ADMIN_PASSWORD"),
        ("teacher", "Преподаватель", "teacher", "TEACHER_PASSWORD"),
        ("trainee", "Обучающийся Иванов", "trainee", "TRAINEE_PASSWORD"),
        ("trainee2", "Обучающийся Петрова", "trainee", "TRAINEE_PASSWORD"),
    ]:
        pw = os.environ.get(env) or f"{login.rstrip('2')}112"
        db.ex("INSERT INTO users(login,name,role,pw_hash,group_id,created) "
              "VALUES(?,?,?,?,?,?)",
              (login, name, rl, hash_pw(pw), gid if rl == "trainee" else None, time.time()))
        made.append({"login": login, "role": rl, "password": pw})
    return made
