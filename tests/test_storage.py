import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from api import db, bkt, session_store as store


from support import DatabaseCase

class StorageTests(DatabaseCase):
    def test_nested_rollback(self):
        with self.assertRaises(RuntimeError):
            with db.tx():
                db.set_setting("outer",1)
                with db.tx():
                    db.set_setting("inner",2)
                raise RuntimeError("injected")
        self.assertIsNone(db.setting("outer"))
        self.assertIsNone(db.setting("inner"))

    def test_migration_repeat_preserves_data(self):
        self.state()
        db.migrate(db.conn()); db.migrate(db.conn())
        self.assertEqual(len(db.q("SELECT * FROM schema_migrations")),len(__import__("api.migrations",fromlist=["MIGRATIONS"]).MIGRATIONS))
        self.assertEqual(db.q1("SELECT state FROM sessions WHERE id='s'")["state"],"live")

    def test_events_and_frozen_rules(self):
        s=self.state()
        with db.tx():
            store.persist(s)
            versions=copy.deepcopy(s["versions"])
            s["kartochka"]["adres_polny"]="улица Тестовая, дом 1"
            store.persist(s); store.persist(s)
        events=db.q("SELECT * FROM session_events ORDER BY seq")
        self.assertEqual([x["type"] for x in events],["session_started","field_changed"])
        self.assertEqual(s["versions"],versions)
        self.assertEqual(store.index(s),store.C.load("index"))

    def test_receipt_once_and_atomic_rollback(self):
        s=self.state(); live={"s":s}
        calls=[]
        @store.command(live, complete=True)
        def complete(sid,u):
            calls.append(sid)
            store.persist(live[sid])
            bkt.primenit(u["id"],[{"kod":"adres","proyden":True}])
            db.ex("UPDATE sessions SET state='done',itog=? WHERE id=?",('{}',sid))
            live.pop(sid)
            return {"ball":100,"passed":True,"fakty":[{"kod":"adres","proyden":True}]}
        original=db.ex
        def fail(sql,args=()):
            if sql.startswith("INSERT INTO completion_receipts"):
                raise RuntimeError("disk failure injection")
            return original(sql,args)
        with patch.object(db,"ex",side_effect=fail), self.assertRaises(RuntimeError):
            complete("s",{"id":1})
        self.assertIn("s",live)
        self.assertEqual(db.q1("SELECT state FROM sessions WHERE id='s'")["state"],"live")
        self.assertEqual(db.q("SELECT * FROM bkt"),[])
        self.assertEqual(db.q("SELECT * FROM session_events"),[])
        result=complete("s",{"id":1})
        self.assertEqual(complete("s",{"id":1}),result)
        self.assertEqual(len(calls),2) # one failed transaction, one success
        self.assertEqual(bkt.profil(1)["adres"]["n"],1)

    def test_programmes_and_unknown_observations(self):
        bkt.primenit(1,[{"kod":"rech","proyden":None},{"kod":"adres","proyden":True}])
        self.assertEqual(bkt.profil(1)["rech"]["n"],0)
        self.assertTrue(all(v["n"]==0 for v in bkt.profil(1,"dds").values()))
        self.assertIsNone(bkt.prognoz_attestacii(1)["vyzovov_do_attestacii"])

    def test_backup_roundtrip(self):
        s=self.state()
        with db.tx(): store.persist(s)
        path=db.backup_now()
        db.ex("DELETE FROM session_events")
        db.restore(path)
        self.assertEqual(len(db.q("SELECT * FROM session_events")),1)
        self.assertEqual(len(db.q("SELECT * FROM schema_migrations")),len(__import__("api.migrations",fromlist=["MIGRATIONS"]).MIGRATIONS))

    def test_backup_names_are_unique_and_complete(self):
        first,second=db.backup_now(),db.backup_now()
        self.assertNotEqual(first,second)
        self.assertTrue(Path(first).is_file() and Path(second).is_file())
        self.assertEqual(list(Path(first).parent.glob('*.tmp')),[])


if __name__=="__main__": unittest.main()
