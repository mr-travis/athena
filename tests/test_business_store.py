"""用真实临时 SQLite 验证持久化，不触碰用户数据库、工作区或模型。"""

from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest

from business_store import LocalSqliteStore


class BusinessStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db_path = Path(temporary.name) / "data" / "store.sqlite"
        self.store = LocalSqliteStore(self.db_path)

    def test_create_and_reopen_preserves_record(self):
        # 包含 SQL 字符的 ID 作为普通数据保存，验证参数化查询。
        run_id = "run'; DROP TABLE runs; --"
        self.store.create_run(run_id)
        record = self.store.get_run(run_id)
        self.assertEqual(record["run_id"], run_id)
        self.assertEqual(record["status"], "running")
        self.assertIsNone(record["finished_at"])
        self.assertEqual(datetime.fromisoformat(record["started_at"]).utcoffset(), timedelta(0))
        self.assertEqual(LocalSqliteStore(self.db_path).get_run(run_id), record)

    def test_success_and_failure_persist_without_changing_start(self):
        for status in ("succeeded", "failed"):
            with self.subTest(status=status):
                self.store.create_run(status)
                started = self.store.get_run(status)["started_at"]
                self.store.finish_run(status, status)
                record = LocalSqliteStore(self.db_path).get_run(status)
                self.assertEqual(record["status"], status)
                self.assertEqual(record["started_at"], started)
                self.assertEqual(datetime.fromisoformat(record["finished_at"]).utcoffset(), timedelta(0))

    def test_duplicate_id_does_not_overwrite(self):
        self.store.create_run("one")
        self.store.finish_run("one", "succeeded")
        before = self.store.get_run("one")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.create_run("one")
        self.assertEqual(self.store.get_run("one"), before)

    def test_missing_run(self):
        self.assertIsNone(self.store.get_run("missing"))
        with self.assertRaises(KeyError):
            self.store.finish_run("missing", "failed")
        self.assertIsNone(self.store.get_run("missing"))

    def test_invalid_inputs_do_not_change_record(self):
        for run_id in ("", "  ", None):
            with self.subTest(run_id=run_id), self.assertRaises(ValueError):
                self.store.create_run(run_id)
        self.store.create_run("one")
        before = self.store.get_run("one")
        for status in ("running", "unknown", ""):
            with self.subTest(status=status), self.assertRaises(ValueError):
                self.store.finish_run("one", status)
        self.assertEqual(self.store.get_run("one"), before)

    def test_finished_record_cannot_be_rewritten(self):
        self.store.create_run("one")
        self.store.finish_run("one", "succeeded")
        before = self.store.get_run("one")
        with self.assertRaises(ValueError):
            self.store.finish_run("one", "failed")
        self.assertEqual(self.store.get_run("one"), before)


if __name__ == "__main__":
    unittest.main()
