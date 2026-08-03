import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import upload_log_db


class UploadLogDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp_dir.name) / "logs" / "history.sqlite3")
        self.raw_log = Path(self.temp_dir.name) / "bypy.success.log"
        self.raw_log.write_text("rapidupload 31023\nnormal upload OK.\n", encoding="utf-8")
        upload_log_db.initialize_database(self.db)

    def tearDown(self):
        self.temp_dir.cleanup()

    def execute(self, command, **kwargs):
        defaults = {"db": self.db, "command": command}
        defaults.update(kwargs)
        upload_log_db.execute(SimpleNamespace(**defaults))

    def test_records_run_and_per_file_success(self):
        self.execute(
            "run-start",
            run_id="run-1",
            mode="per-file",
            slice_size="1536M",
            retry_count=1,
            pending_count=1,
            pending_bytes=123,
        )
        with closing(sqlite3.connect(self.db)) as connection:
            run = connection.execute(
                "SELECT status, pending_count FROM runs WHERE run_id='run-1'"
            ).fetchone()
        self.assertEqual(run, ("running", 1))

        file_start_args = upload_log_db.build_parser().parse_args(
            [
                "--db", self.db, "file-start", "--run-id", "run-1",
                "--sequence", "1", "--relative-path", "a.ts",
                "--local-path", "/a.ts", "--remote-path", "/live_audio/a.ts",
                "--extension", "ts", "--local-size", "123",
            ]
        )
        output = StringIO()
        with redirect_stdout(output):
            upload_log_db.execute(file_start_args)
        file_id = int(output.getvalue().strip())

        self.execute(
            "file-finish",
            file_id=file_id,
            status="success",
            elapsed_seconds=2,
            process_exit_code=0,
            remote_size=123,
            error_code=None,
            error_category=None,
            error_message=None,
            detail_log_path="a.success.log",
            rapidupload_fallback=1,
            raw_output_file=str(self.raw_log),
            local_deleted=1,
        )
        self.execute(
            "run-finish",
            run_id="run-1",
            status="success",
            success_count=1,
            failed_count=0,
            elapsed_seconds=2,
        )

        with closing(sqlite3.connect(self.db)) as connection:
            file_row = connection.execute(
                """SELECT status, remote_size, rapidupload_fallback,
                          raw_output, local_deleted FROM files"""
            ).fetchone()
            run_status = connection.execute("SELECT status FROM runs").fetchone()[0]
        self.assertEqual(
            file_row,
            ("success", 123, 1, "rapidupload 31023\nnormal upload OK.\n", 1),
        )
        self.assertEqual(run_status, "success")

    def test_event_json_is_validated_and_stored(self):
        self.execute(
            "run-start",
            run_id="run-2",
            mode="per-file",
            slice_size="1536M",
            retry_count=1,
            pending_count=0,
            pending_bytes=0,
        )
        self.execute(
            "event",
            run_id="run-2",
            file_id=None,
            level="warning",
            type="stage_conflict",
            message="保留源文件",
            details_json='{"count": 1}',
        )
        with closing(sqlite3.connect(self.db)) as connection:
            stored = connection.execute("SELECT details_json FROM events").fetchone()[0]
        self.assertEqual(json.loads(stored), {"count": 1})

    def test_failure_view_is_available_for_ai_queries(self):
        with closing(sqlite3.connect(self.db)) as connection:
            columns = connection.execute(
                "PRAGMA table_info(ai_recent_failures)"
            ).fetchall()
        self.assertIn("error_category", [column[1] for column in columns])

    def test_existing_database_is_migrated_without_losing_rows(self):
        old_db = str(Path(self.temp_dir.name) / "old.sqlite3")
        with closing(sqlite3.connect(old_db)) as connection, connection:
            old_schema = upload_log_db.SCHEMA.replace(
                "    rapidupload_fallback INTEGER NOT NULL DEFAULT 0,\n", ""
            ).replace("    raw_output TEXT,\n", "")
            connection.executescript(old_schema)
            connection.execute(
                """INSERT INTO runs(
                    run_id, started_at, status, mode, slice_size, retry_count
                ) VALUES ('old-run', 'now', 'success', 'per-file', '1G', 1)"""
            )
            connection.execute(
                """INSERT INTO files(
                    file_id, run_id, sequence_no, relative_path, local_path,
                    remote_path, local_size, status, started_at
                ) VALUES (
                    1, 'old-run', 1, 'kept.ts', '/kept.ts',
                    '/live_audio/kept.ts', 10, 'success', 'now'
                )"""
            )
        upload_log_db.initialize_database(old_db)
        with closing(sqlite3.connect(old_db)) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(files)")
            }
            path = connection.execute(
                "SELECT relative_path FROM files WHERE file_id=1"
            ).fetchone()[0]
        self.assertIn("rapidupload_fallback", columns)
        self.assertIn("raw_output", columns)
        self.assertEqual(path, "kept.ts")


if __name__ == "__main__":
    unittest.main()
