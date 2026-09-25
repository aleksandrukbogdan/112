import json
import tempfile
import unittest
from pathlib import Path
from api import db, config as C

class DatabaseCase(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        if db._conn: db._conn.close()
        db._conn=None
        db.PG=False
        db.DB_PATH=Path(self.tmp.name)/"test.db"
        db.BACKUP_DIR=Path(self.tmp.name)/"backup"
        C.DATA=Path(__file__).resolve().parents[1]/"data"
        db.conn()

    def tearDown(self):
        db._conn.close();db._conn=None
        self.tmp.cleanup()

    def state(self):
        s={"id":"s","user_id":1,"bilet":{"id":"example"},"kartochka":{},"dialog":[]}
        db.ex("INSERT INTO sessions(id,user_id,scenario_id,state,data) VALUES(?,?,?,?,?)",
              ("s",1,"example","live",json.dumps(s)))
        return s
