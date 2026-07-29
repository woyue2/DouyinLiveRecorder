from __future__ import annotations

import os
from pathlib import Path


def get_working_output_path(final_path: str) -> str:
    return f"{final_path}.part"


def get_working_output_files(working_path: str) -> list[Path]:
    path = Path(working_path)
    if "%03d" in path.name:
        return sorted(path.parent.glob(path.name.replace("%03d", "*")))
    return [path] if path.exists() else []


def publish_output_files(working_path: str) -> list[str]:
    published = []
    for candidate in get_working_output_files(working_path):
        if candidate.suffix != ".part":
            continue
        final_path = candidate.with_suffix("")
        os.replace(candidate, final_path)
        published.append(str(final_path))
    return published


def publish_completed_segments(working_pattern: str) -> list[str]:
    path = Path(working_pattern)
    if "%03d" not in path.name:
        return []

    candidates = get_working_output_files(working_pattern)
    published = []
    for candidate in candidates[:-1]:
        if candidate.suffix != ".part":
            continue
        final_path = candidate.with_suffix("")
        os.replace(candidate, final_path)
        published.append(str(final_path))
    return published
