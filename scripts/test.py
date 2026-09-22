#!/usr/bin/env python3
"""Приёмочный тест. Без docker, на временной базе.  python3 scripts/test.py"""
import os, sys, tempfile
from pathlib import Path
R = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(R))
tmp = tempfile.mkdtemp()
os.environ.update(DATA_DIR=str(R / "data"), DB_PATH=f"{tmp}/t.db", BACKUP_DIR=f"{tmp}/bk",
                  LLM_ENABLED="false", VOICE_URL="http://127.0.0.1:1", ASR_URL="http://127.0.0.1:2")
from fastapi.testclient import TestClient
from api.main import app
from api import domain as D, bkt

ok = fail = 0
def chk(n, c, d=""):
    global ok, fail
    ok += bool(c); fail += not c
    print(f"  [{'ok' if c else 'FAIL'}]{' ' if c else ''}  {n} {d}")

with TestClient(app) as c:
    L = lambda l, p: c.post("/api/login", json={"login": l, "password": p}).json()
    tr, te, ad = L("trainee", "trainee112"), L("teacher", "teacher112"), L("admin", "admin112")
    H = lambda t: {"Authorization": "Bearer " + t["token"]}

    print("\n1. Данные")
    s = c.get("/api/config", headers=H(tr)).json()["svodka"]
    chk("классификатор 1283 позиции", s["klassifikator"]["poziciy"] == 1283)
    chk("96 вызовов, 22 с эталонным адресом", s["bilety"]["vsego"] == 96 and s["bilety"]["s_utochneniem"] == 22)

    print("\n2. Роли и права")
    chk("три роли входят", tr["user"]["role"] == "trainee" and te["user"]["role"] == "teacher" and ad["user"]["role"] == "admin")
    chk("без входа — 401", c.get("/api/config").status_code == 401)
    chk("неверный пароль — 401", c.post("/api/login", json={"login": "admin", "password": "x"}).status_code == 401)
    chk("обучающийся не видит админку", c.get("/api/admin/system", headers=H(tr)).status_code == 403)
    chk("администратор НЕ меняет оценки (ТЗ)", c.post("/api/session/x/override", json={"ball": 1, "reason": "xxxxx"}, headers=H(ad)).status_code == 403)

    print("\n3. Сверка адреса, речь, грамматика")
    chk("полный адрес засчитан", D.sravnit_adres("Москва, МЖД Киевская, 1 км, строение 2", "Москва, МЖД Киевская, 1 км, стр. 2")["sovpalo"])
    chk("ориентир не засчитан", not D.sravnit_adres("около станции Киевская", "Москва, МЖД Киевская, 1 км, стр. 2")["sovpalo"])
    chk("речь: «не паникуйте»", any(x["pravilo"] == "chastica_ne" for x in D.proverit_rech("Не паникуйте")))
    chk("грамматика: «короче»", bool(D.proverit_grammatiku("Короче там пожар в доме")))

    print("\n4. Генерация и утверждение сценариев")
    g = c.post("/api/scenarios/generate", json={"kod": "1010101", "kolichestvo": 1}, headers=H(te)).json()
    vid = lambda: any(x["id"] == g[0]["id"] for x in c.get("/api/scenarios", headers=H(tr)).json())
    chk("черновик создан", g[0]["kod_etalon"] == "1010101")
    chk("черновик скрыт от обучающегося", not vid())
    chk("обучающийся не утверждает", c.post(f"/api/scenarios/{g[0]['id']}/validate", json={"status": "published"}, headers=H(tr)).status_code == 403)
    c.post(f"/api/scenarios/{g[0]['id']}/validate", json={"status": "published"}, headers=H(te))
    chk("после утверждения виден", vid())

    print("\n5. Назначение и занятие")
    gid = c.get("/api/groups", headers=H(te)).json()[0]["id"]
    c.post("/api/assignments", json={"group_id": gid, "scenario_ids": ["b01_v1"], "tayming_sec": 45}, headers=H(te))
    a = c.get("/api/assignments", headers=H(tr)).json()
    chk("задание дошло до обучающегося", len(a) == 1)
    ss = c.post("/api/session", json={"scenario_id": "b01_v1", "assignment_id": a[0]["id"]}, headers=H(tr)).json()
    sid = ss["session_id"]
    chk("лимит взят из назначения", ss["tayming_sec"] == 45)
    r = c.post(f"/api/session/{sid}/replika", json={"tekst": "Где это?"}, headers=H(tr)).json()
    chk("на общий вопрос — ориентир", "МЖД" not in r["otvet"])
    r = c.post(f"/api/session/{sid}/replika", json={"tekst": "Уточните номер строения"}, headers=H(tr)).json()
    chk("на уточнение — эталон", "МЖД" in r["otvet"] and r["adres_raskryt"])
    chk("преподаватель видит занятие вживую", len(c.get("/api/live", headers=H(te)).json()) == 1)
    chk("чужое занятие недоступно", c.post(f"/api/session/{sid}/replika", json={"tekst": "x"}, headers=H(te)).status_code == 403)
    av = c.post(f"/api/session/{sid}/pole", json={"key": "tip_kod", "value": "1010101"}, headers=H(tr)).json()["avto"]
    chk("пожар: мусор → 101", av["sluzhby"] == ["101"])
    av = c.post(f"/api/session/{sid}/pole", json={"key": "postradavshie", "value": "est"}, headers=H(tr)).json()["avto"]
    chk("+ пострадавшие → СМП", "SMP" in av["sluzhby"])
    chk("прогноз внутри вызова", 0 <= c.get(f"/api/session/{sid}/prognoz", headers=H(tr)).json()["veroyatnost"] <= 1)
    it = c.post(f"/api/session/{sid}/otpravit", headers=H(tr), json={"sluzhby": av["sluzhby"], "kartochka": {
        "opisanie": "Возгорание мусорного контейнера, пострадавших нет.",
        "adres_polny": "Москва, МЖД Киевская, 1 км, строение 2",
        "zayavitel_fio": "Сидоров Иван Сергеевич", "zayavitel_telefon": "916-126-34-71"}}).json()
    chk("граф: 8 фактов с источником", len(it["fakty"]) == 8 and all(f["istochnik"] for f in it["fakty"]))
    chk("верный ответ — 100 баллов", it["ball"] == 100, str(it["ball"]))
    chk("BKT обновился", it["bkt"]["adres"]["n"] == 1)

    print("\n6. Оценка, аналитика, прогноз")
    chk("преподаватель меняет оценку", c.post(f"/api/session/{sid}/override", json={"ball": 90, "reason": "проверка методистом"}, headers=H(te)).json()["ok"])
    chk("изменение — в журнале аудита", any(x["action"] == "grade_override" for x in c.get("/api/admin/audit", headers=H(ad)).json()))
    me = c.get("/api/analytics/me", headers=H(tr)).json()
    chk("прогноз вызова проверен фактом", me["prognozy_vyzov"]["vsego"] == 1)
    chk("прогноз до освоения", me["attestaciya"]["vyzovov_do_attestacii"] >= 0)
    grp = c.get(f"/api/analytics/group/{gid}", headers=H(te)).json()
    chk("аналитика группы: таблица и тепловая карта", len(grp["obuchayushchiesya"]) == 2 and len(grp["heatmap"]) > 0)
    chk("BKT: 4 верных подряд → освоено", (lambda p: [p := bkt.obnovit(p, True) for _ in range(4)][-1])(bkt.P_INIT) >= bkt.MASTERY)

    print("\n7. Отчёты и администрирование")
    chk("форма 1/112", len(c.get("/api/otchet/forma1", headers=H(te)).json()["stroki"]) == 10)
    chk("форма 2/112", len(c.get("/api/otchet/forma2", headers=H(te)).json()["po_napravleniyam"]) == 6)
    p = c.get(f"/api/otchet/zanyatie/{sid}.pdf?token={tr['token']}")
    chk("отчёт о занятии PDF", p.status_code == 200 and p.content[:4] == b"%PDF")
    p = c.get(f"/api/otchet/sertifikat/{tr['user']['id']}.pdf?token={tr['token']}")
    chk("сертификат PDF", p.status_code == 200 and p.content[:4] == b"%PDF")
    p = c.get(f"/api/otchet/export.xlsx?token={te['token']}")
    chk("выгрузка Excel", p.status_code == 200 and p.content[:2] == b"PK")
    bk = c.post("/api/admin/backup", headers=H(ad)).json()["file"]
    chk("резервная копия", bk.endswith(".json.gz"))
    from api import db as _db
    n_before = _db.q1("SELECT COUNT(*) AS n FROM sessions")["n"]
    _db.ex("DELETE FROM sessions")
    rest = _db.restore(str(_db.BACKUP_DIR / bk))
    chk("восстановление из копии", _db.q1("SELECT COUNT(*) AS n FROM sessions")["n"] == n_before and rest["users"] >= 4)
    chk("создание пользователя", "id" in c.post("/api/users", json={"login": "t3", "name": "Тест", "role": "trainee", "password": "secret1", "group_id": gid}, headers=H(ad)).json())
    chk("без голоса — корректный отказ 503", c.post("/api/voice/asr", files={"audio": ("a.webm", b"x", "audio/webm")}, headers=H(tr)).status_code == 503)
    chk("интерфейс отдаётся", "Тренажёр" in c.get("/").text and c.get("/static/app.js").status_code == 200)


    print("\n8. Режим «Диспетчер ДДС»")
    from api import dds as DDS, config as CC
    import time as _t
    CC.DDS["etapy_sec"] = {"pribytie": 1, "raboty": 1, "zaversheno": 1}
    CC.DDS["veroyatnost_oshibki"] = 1.0
    chk("справочник служб ≥190", len(c.get("/api/dds/sluzhby", headers=H(tr)).json()) >= 190)
    chk("51 тип «что случилось»", len(c.get("/api/config", headers=H(tr)).json()["chto_sluchilos"]) == 51)
    r = c.post("/api/dds/session", json={"scenario_id": "b01_v2"}, headers=H(tr)).json()
    ds = r["session_id"]; kart = r["kartochka"]
    chk("карточка поступила без эталона", "_pravda" not in kart and "_oshibka" not in kart)
    chk("есть запись разговора 112", len(r["zapis"]) >= 8)
    chk("есть бригады и руководитель в телефоне", sum(1 for x in r["kontakty"] if x["kto"] == "brigada") == 3)
    osh = DDS.LIVE[ds]["kartochka"]["_oshibka"]
    chk("система внесла ошибку оператора 112", osh is not None, str(osh and osh["vid"]))
    st = c.post(f"/api/dds/{ds}/status", json={"status": "prinyata", "kommentariy": "Принято в работу"}, headers=H(tr)).json()
    chk("«Принята» — основание есть", st["osnovanie_bylo"])
    bad = c.post(f"/api/dds/{ds}/status", json={"status": "zaversheno", "kommentariy": "x"}, headers=H(tr)).json()
    chk("«Работы завершены» без основания — предупреждение", not bad["osnovanie_bylo"])
    DDS.LIVE[ds]["statusy"].pop()           # убрать ошибочный статус для чистоты дальнейших проверок
    pr = DDS.LIVE[ds]["kartochka"]["_pravda"]
    c.post(f"/api/dds/{ds}/zamechanie", json={"pole": osh["pole"], "verno": osh["pravda"] if osh["pole"] != "postradavshie" else DDS.POST_RU[osh["pravda"]]}, headers=H(tr))
    cl = c.post(f"/api/dds/{ds}/call", json={"kontakt": "br1"}, headers=H(tr)).json()
    while cl["ishod"] != "otvetil":
        cl = c.post(f"/api/dds/{ds}/call", json={"kontakt": "br1"}, headers=H(tr)).json()
    o1 = c.post(f"/api/dds/{ds}/call/{cl['call_id']}/say", json={"tekst": "Бригада, выезд на драку"}, headers=H(tr)).json()
    chk("без адреса бригада не выезжает", not o1["vyezd"], o1["otvet"])
    tel4 = pr["telefon"]
    o2 = c.post(f"/api/dds/{ds}/call/{cl['call_id']}/say", json={"tekst": f"Адрес: {pr['adres']}. {pr['opisanie']} Заявитель {tel4}"}, headers=H(tr)).json()
    chk("с адресом — выезжает", o2["vyezd"], o2["otvet"])
    c.post(f"/api/dds/{ds}/call/{cl['call_id']}/end", headers=H(tr))
    c.post(f"/api/dds/{ds}/status", json={"status": "nachalo"}, headers=H(tr))
    for faza, kod in (("pribytie", "pribytie"), ("raboty", "raboty"), ("zaversheno", "zaversheno")):
        _t.sleep(1.1)
        ev = c.get(f"/api/dds/{ds}/events", headers=H(tr)).json()["vhodyashchie"]
        e = next((x for x in ev if x["faza"] == faza), None)
        if e:
            c.post(f"/api/dds/{ds}/incoming/{e['id']}/answer", headers=H(tr))
        c.post(f"/api/dds/{ds}/status", json={"status": kod, "kommentariy": "Работы выполнены" if kod == "zaversheno" else ""}, headers=H(tr))
    chk("старший бригады звонит на этапах", all(x["status"] == "prinyat" for x in DDS.LIVE[ds]["sobytiya"]))
    live_dds = [x for x in c.get("/api/live", headers=H(te)).json() if x.get("rezhim") == "ДДС"]
    chk("преподаватель видит занятие ДДС", len(live_dds) == 1)
    it = c.post(f"/api/dds/{ds}/finish", headers=H(tr)).json()
    fk = {f["kod"]: f["proyden"] for f in it["fakty"]}
    chk("граф ДДС: 9 фактов с источником", len(it["fakty"]) == 9 and all(f["istochnik"] for f in it["fakty"]))
    chk("ошибка найдена и засчитана", fk["proverka"])
    chk("передача бригаде засчитана", fk["peredacha"])
    chk("статусы по факту засчитаны", fk["po_faktu"])
    chk("правильный ДДС-вызов — 100 баллов", it["ball"] == 100, str(it["ball"]) + " " + str([k for k, v in fk.items() if not v]))
    r2 = c.post("/api/dds/session", json={"scenario_id": "b01_v1"}, headers=H(tr)).json()
    s2 = r2["session_id"]
    c.post(f"/api/dds/{s2}/status", json={"status": "zaversheno", "kommentariy": "всё"}, headers=H(tr))
    it2 = c.post(f"/api/dds/{s2}/finish", headers=H(tr)).json()
    f2 = {f["kod"]: f["proyden"] for f in it2["fakty"]}
    chk("статус без основания — не засчитан", not f2["po_faktu"] and not f2["podtverzhdenie"])
    chk("ДДС-занятия в аналитике", c.get("/api/analytics/me", headers=H(tr)).json()["n"] >= 3)
    p = c.get(f"/api/otchet/zanyatie/{ds}.pdf?token={tr['token']}")
    chk("PDF-отчёт по занятию ДДС", p.status_code == 200 and p.content[:4] == b"%PDF")

print(f"\n{'=' * 44}\n  пройдено: {ok}, провалено: {fail}\n{'=' * 44}")
sys.exit(1 if fail else 0)
