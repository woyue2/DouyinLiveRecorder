#!/usr/bin/env python3
"""Persistent structured upload history for upload_baidu.sh.

Only Python's standard library is used so the recorder host needs no extra
package.  The command line intentionally prints only generated identifiers or
JSON reports; human-facing progress remains the shell script's responsibility.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    mode TEXT NOT NULL,
    slice_size TEXT NOT NULL,
    retry_count INTEGER NOT NULL,
    pending_count INTEGER NOT NULL DEFAULT 0,
    pending_bytes INTEGER NOT NULL DEFAULT 0,
    success_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    elapsed_seconds INTEGER
);

CREATE TABLE IF NOT EXISTS files (
    file_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    sequence_no INTEGER NOT NULL,
    relative_path TEXT NOT NULL,
    local_path TEXT NOT NULL,
    remote_path TEXT NOT NULL,
    extension TEXT,
    local_size INTEGER NOT NULL,
    remote_size INTEGER,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    elapsed_seconds INTEGER,
    process_exit_code INTEGER,
    error_code TEXT,
    error_category TEXT,
    error_message TEXT,
    detail_log_path TEXT,
    rapidupload_fallback INTEGER NOT NULL DEFAULT 0,
    raw_output TEXT,
    local_deleted INTEGER NOT NULL DEFAULT 0,
    UNIQUE(run_id, sequence_no)
);

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    run_id TEXT REFERENCES runs(run_id),
    file_id INTEGER REFERENCES files(file_id),
    level TEXT NOT NULL,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    details_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_files_run_status
    ON files(run_id, status);
CREATE INDEX IF NOT EXISTS idx_files_error
    ON files(error_category, finished_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_run_time
    ON events(run_id, occurred_at);

CREATE VIEW IF NOT EXISTS ai_recent_failures AS
SELECT
    f.file_id,
    f.run_id,
    f.finished_at,
    f.relative_path,
    f.local_size,
    f.remote_size,
    f.process_exit_code,
    f.error_code,
    f.error_category,
    f.error_message,
    f.detail_log_path
FROM files AS f
WHERE f.status = 'failed';
"""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect(db_path: str) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


def initialize_database(db_path: str) -> None:
    with closing(connect(db_path)) as connection:
        with connection:
            connection.executescript(SCHEMA)
            # CREATE TABLE does not add columns to an existing installation.
            # Keep migrations additive so historical upload data is preserved.
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(files)")
            }
            if "rapidupload_fallback" not in columns:
                connection.execute(
                    "ALTER TABLE files ADD COLUMN rapidupload_fallback "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            if "raw_output" not in columns:
                connection.execute("ALTER TABLE files ADD COLUMN raw_output TEXT")


def row_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {key: row[key] for key in row.keys()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Structured bypy upload log")
    parser.add_argument("--db", required=True, help="SQLite database path")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init")

    run_start = sub.add_parser("run-start")
    run_start.add_argument("--run-id", required=True)
    run_start.add_argument("--mode", default="per-file")
    run_start.add_argument("--slice-size", required=True)
    run_start.add_argument("--retry-count", required=True, type=int)
    run_start.add_argument("--pending-count", required=True, type=int)
    run_start.add_argument("--pending-bytes", required=True, type=int)

    run_finish = sub.add_parser("run-finish")
    run_finish.add_argument("--run-id", required=True)
    run_finish.add_argument("--status", required=True)
    run_finish.add_argument("--success-count", required=True, type=int)
    run_finish.add_argument("--failed-count", required=True, type=int)
    run_finish.add_argument("--elapsed-seconds", required=True, type=int)

    file_start = sub.add_parser("file-start")
    file_start.add_argument("--run-id", required=True)
    file_start.add_argument("--sequence", required=True, type=int)
    file_start.add_argument("--relative-path", required=True)
    file_start.add_argument("--local-path", required=True)
    file_start.add_argument("--remote-path", required=True)
    file_start.add_argument("--extension", default="")
    file_start.add_argument("--local-size", required=True, type=int)

    file_finish = sub.add_parser("file-finish")
    file_finish.add_argument("--file-id", required=True, type=int)
    file_finish.add_argument("--status", required=True, choices=("success", "failed"))
    file_finish.add_argument("--elapsed-seconds", required=True, type=int)
    file_finish.add_argument("--process-exit-code", type=int)
    file_finish.add_argument("--remote-size", type=int)
    file_finish.add_argument("--error-code")
    file_finish.add_argument("--error-category")
    file_finish.add_argument("--error-message")
    file_finish.add_argument("--detail-log-path", required=True)
    file_finish.add_argument(
        "--rapidupload-fallback", required=True, type=int, choices=(0, 1)
    )
    file_finish.add_argument("--raw-output-file", required=True)
    file_finish.add_argument("--local-deleted", required=True, type=int, choices=(0, 1))

    event = sub.add_parser("event")
    event.add_argument("--run-id")
    event.add_argument("--file-id", type=int)
    event.add_argument("--level", required=True)
    event.add_argument("--type", required=True)
    event.add_argument("--message", required=True)
    event.add_argument("--details-json")

    report = sub.add_parser("ai-report")
    report.add_argument("--limit", type=int, default=20)
    return parser


def execute(args: argparse.Namespace) -> None:
    initialize_database(args.db)
    if args.command == "init":
        return

    with closing(connect(args.db)) as connection, connection:
        if args.command == "run-start":
            connection.execute(
                """INSERT INTO runs(
                       run_id, started_at, status, mode, slice_size, retry_count,
                       pending_count, pending_bytes
                   ) VALUES (?, ?, 'running', ?, ?, ?, ?, ?)""",
                (args.run_id, now_iso(), args.mode, args.slice_size,
                 args.retry_count, args.pending_count, args.pending_bytes),
            )
        elif args.command == "run-finish":
            connection.execute(
                """UPDATE runs SET finished_at=?, status=?, success_count=?,
                       failed_count=?, elapsed_seconds=? WHERE run_id=?""",
                (now_iso(), args.status, args.success_count, args.failed_count,
                 args.elapsed_seconds, args.run_id),
            )
        elif args.command == "file-start":
            cursor = connection.execute(
                """INSERT INTO files(
                       run_id, sequence_no, relative_path, local_path, remote_path,
                       extension, local_size, status, started_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, 'uploading', ?)""",
                (args.run_id, args.sequence, args.relative_path, args.local_path,
                 args.remote_path, args.extension, args.local_size, now_iso()),
            )
            print(cursor.lastrowid)
        elif args.command == "file-finish":
            raw_output = Path(args.raw_output_file).read_text(
                encoding="utf-8", errors="replace"
            )
            connection.execute(
                """UPDATE files SET finished_at=?, status=?, elapsed_seconds=?,
                       process_exit_code=?, remote_size=?, error_code=?,
                       error_category=?, error_message=?, detail_log_path=?,
                       rapidupload_fallback=?, raw_output=?, local_deleted=?
                   WHERE file_id=?""",
                (now_iso(), args.status, args.elapsed_seconds,
                 args.process_exit_code, args.remote_size, args.error_code,
                 args.error_category, args.error_message, args.detail_log_path,
                 args.rapidupload_fallback, raw_output, args.local_deleted,
                 args.file_id),
            )
        elif args.command == "event":
            details = args.details_json
            if details:
                details = json.dumps(json.loads(details), ensure_ascii=False)
            connection.execute(
                """INSERT INTO events(
                       occurred_at, run_id, file_id, level, event_type, message,
                       details_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (now_iso(), args.run_id, args.file_id, args.level, args.type,
                 args.message, details),
            )
        elif args.command == "ai-report":
            runs = connection.execute(
                "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?",
                (args.limit,),
            ).fetchall()
            failures = connection.execute(
                "SELECT * FROM ai_recent_failures ORDER BY finished_at DESC LIMIT ?",
                (args.limit,),
            ).fetchall()
            files = connection.execute(
                """SELECT
                       file_id, run_id, sequence_no, relative_path, local_path,
                       remote_path, extension, local_size, remote_size, status,
                       started_at, finished_at, elapsed_seconds,
                       process_exit_code, error_code, error_category,
                       error_message, detail_log_path, rapidupload_fallback,
                       local_deleted,
                       substr(raw_output, -12000) AS raw_output_tail
                   FROM files ORDER BY file_id DESC LIMIT ?""",
                (args.limit,),
            ).fetchall()
            active = connection.execute(
                """SELECT * FROM files WHERE status='uploading'
                   ORDER BY started_at DESC LIMIT ?""",
                (args.limit,),
            ).fetchall()
            print(json.dumps(
                {
                    "generated_at": now_iso(),
                    "recent_runs": [row_dict(row) for row in runs],
                    "recent_files": [row_dict(row) for row in files],
                    "recent_failures": [row_dict(row) for row in failures],
                    "unfinished_files": [row_dict(row) for row in active],
                },
                ensure_ascii=False,
                indent=2,
            ))


def main() -> None:
    args = build_parser().parse_args()
    execute(args)


if __name__ == "__main__":
    main()
