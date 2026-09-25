"""Opt-in integration against PostgreSQL. Creates and drops ONLY its own random schema.

RUN_POSTGRES_TESTS=1 TEST_DATABASE_URL=postgresql://... python -m unittest discover -s tests/integration -p test_postgres.py -v
Prefer a disposable test database. Never provide a production superuser account.
"""
import os
import unittest
import uuid

class PostgreSQLTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if os.environ.get("RUN_POSTGRES_TESTS")!="1" or not os.environ.get("TEST_DATABASE_URL"):
            raise unittest.SkipTest("PostgreSQL test DB not explicitly enabled")
        import psycopg
        from psycopg import sql
        from psycopg.rows import dict_row
        from api import db
        cls.db=db;cls.sql=sql;cls.schema="t112_patch_test_"+uuid.uuid4().hex
        cls.c=psycopg.connect(os.environ["TEST_DATABASE_URL"],row_factory=dict_row)
        cls.c.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema)))
        cls.c.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(cls.schema)))
        for statement in db._schema(True).split(";"):
            if statement.strip():cls.c.execute(statement)
        cls.c.commit()
        db.PG=True;db._conn=cls.c;db.migrate(cls.c)

    @classmethod
    def tearDownClass(cls):
        cls.c.rollback()
        cls.c.execute(cls.sql.SQL("DROP SCHEMA {} CASCADE").format(cls.sql.Identifier(cls.schema)))
        cls.c.commit();cls.c.close();cls.db._conn=None;cls.db.PG=False

    def test_transaction_and_returning_id(self):
        db=self.db
        with self.assertRaises(RuntimeError):
            with db.tx():
                db.ex("INSERT INTO groups(name) VALUES(?)",("rolled-back",))
                db.set_setting("no",1)
                raise RuntimeError("injected")
        self.assertIsNone(db.q1("SELECT * FROM groups WHERE name=?",("rolled-back",)))
        with db.tx():
            identifier=db.ex("INSERT INTO groups(name) VALUES(?)",("committed",))
            db.set_setting("yes",2)
        self.assertIsInstance(identifier,int)
        self.assertEqual(db.setting("yes"),2)
        self.assertEqual(len(db.q("SELECT * FROM schema_migrations")),1)

if __name__=="__main__":unittest.main()
