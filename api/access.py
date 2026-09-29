"""Server-side role and group scopes, shared by reports, lessons and commands."""
from fastapi import HTTPException
from . import db


def groups(user):
    if user['role'] == 'admin':
        return {r['id'] for r in db.q('SELECT id FROM groups')}
    if user['role'] == 'teacher':
        return {r['group_id'] for r in db.q('SELECT group_id FROM teacher_groups WHERE teacher_id=?', (user['id'],))}
    return {user['group_id']} if user.get('group_id') else set()


def group(user, gid):
    if user['role'] != 'admin' and gid not in groups(user):
        raise HTTPException(403, 'Группа не закреплена за преподавателем')


def learner(user, uid):
    row = db.q1('SELECT id,role,group_id,name FROM users WHERE id=?', (uid,))
    if not row:
        raise HTTPException(404, 'Пользователь не найден')
    if user['id'] == uid or user['role'] == 'admin':
        return row
    if user['role'] == 'trainee' or row.get('group_id') not in groups(user):
        raise HTTPException(403, 'Нет доступа к этому обучающемуся')
    return row


def session(user, sid, completed=False):
    row = db.q1('SELECT * FROM sessions WHERE id=?', (sid,))
    if not row:
        raise HTTPException(404, 'Попытка не найдена')
    learner(user, row['user_id'])
    if completed and row['state'] != 'done':
        raise HTTPException(409, 'Разбор доступен после завершения попытки')
    return row


def scenario(user, row, edit=False):
    if not row:
        raise HTTPException(404, 'Сценарий не найден')
    if row.get('status')!='published' and user['role']=='teacher' and row.get('created_by') not in (None,user['id']):
        raise HTTPException(403,'Черновик другого преподавателя')
    if edit:
        if user['role'] != 'teacher':
            raise HTTPException(403, 'Изменять и утверждать сценарии может преподаватель')
        if row.get('created_by') not in (None, user['id']):
            raise HTTPException(403, 'Чужой сценарий доступен для просмотра, создайте свою копию')
        if db.q1("SELECT id FROM sessions WHERE scenario_id=? AND state='live'", (row['id'],)):
            raise HTTPException(409, 'Сценарий используется в занятии; создайте новую версию-копию')


def bootstrap():
    """Explicit grants from old assignments; a single legacy teacher owns old groups."""
    if db.setting('scope_migration_2'):
        return
    with db.tx():
        for row in db.q('SELECT DISTINCT created_by,group_id FROM assignments WHERE created_by IS NOT NULL'):
            db.ex('INSERT INTO teacher_groups(teacher_id,group_id) VALUES(?,?) ON CONFLICT DO NOTHING',
                  (row['created_by'], row['group_id']))
        teachers = db.q("SELECT id FROM users WHERE role='teacher' AND active=1")
        if len(teachers) == 1:
            for row in db.q('SELECT id FROM groups'):
                db.ex('INSERT INTO teacher_groups(teacher_id,group_id) VALUES(?,?) ON CONFLICT DO NOTHING',
                      (teachers[0]['id'], row['id']))
        db.set_setting('scope_migration_2', True)
