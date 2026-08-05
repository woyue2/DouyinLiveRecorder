from __future__ import annotations

import json
import os
import signal
from pathlib import Path
from typing import BinaryIO, TextIO


def get_ffmpeg_input_url(command: list[str]) -> str:
    try:
        return command[command.index("-i") + 1]
    except (ValueError, IndexError):
        return ""


def get_ffmpeg_log_path(output_path: str) -> str:
    path = Path(output_path)
    log_name = f"{path.name.replace('%03d', 'segments')}.ffmpeg.log"
    return str(path.with_name(log_name))


def write_ffmpeg_log_header(
    log_file: TextIO,
    record_name: str,
    command: list[str],
) -> None:
    log_file.write(f"record_name: {record_name}\n")
    log_file.write(f"input_url: {get_ffmpeg_input_url(command)}\n")
    log_file.write(
        "command_json: "
        + json.dumps(command, ensure_ascii=False)
        + "\n\n"
    )
    log_file.flush()


def copy_ffmpeg_output(output: BinaryIO, log_file: TextIO) -> None:
    try:
        for raw_line in iter(output.readline, b""):
            log_file.write(raw_line.decode("utf-8", errors="replace"))
            log_file.flush()
    finally:
        output.close()


def describe_return_code(return_code: int | None) -> str:
    if return_code is None:
        return "running"
    if return_code >= 0 or os.name == "nt":
        return str(return_code)

    signal_number = -return_code
    try:
        signal_name = signal.Signals(signal_number).name
    except ValueError:
        signal_name = "UNKNOWN"
    return f"{return_code} (signal {signal_number}: {signal_name})"
