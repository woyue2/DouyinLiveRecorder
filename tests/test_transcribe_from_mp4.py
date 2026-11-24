# -*- coding: utf-8 -*-
"""回歸測試：只錄 TS 的場次要靠轉碼後的 MP4 觸發轉寫。

使用者要求有痕/犬傲天不要 mp3（省掉那條音軌的體積），但 转MD 依賴音訊。
轉碼指令是 `-c:a copy`，MP4 裡的音軌跟原始流一模一樣，拿它送 ASR 完全等價。

反面同樣要守住：TS|MP3 場次已經用 mp3 轉寫過了，不能再拿 mp4 轉一遍。
"""

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
MAIN_SRC = (ROOT / "main.py").read_text(encoding="utf-8")


def _load_gate():
    tree = ast.parse(MAIN_SRC)
    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "_needs_transcribe_after_transcode")
    ns = {"Path": Path}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), "<gate>", "exec"), ns)
    return ns["_needs_transcribe_after_transcode"]


def test_ts_only_session_transcribes_from_mp4(tmp_path):
    """没有 md（TS-only 或上次转写失败）才补转写。"""
    seg = tmp_path / "a.ts"
    seg.write_bytes(b"x")
    gate = _load_gate()
    assert gate(str(seg), True) is True


def test_existing_md_means_already_transcribed(tmp_path):
    """TS|MP3 由 mp3 转写过后已存在同名 md，不再拿 mp4 重复转。"""
    seg = tmp_path / "a.ts"
    seg.write_bytes(b"x")
    (tmp_path / "a.md").write_text("# x", encoding="utf-8")
    gate = _load_gate()
    assert gate(str(seg), True) is False


def test_streamer_without_transcribe_flag_is_skipped(tmp_path):
    seg = tmp_path / "a.ts"
    seg.write_bytes(b"x")
    gate = _load_gate()
    assert gate(str(seg), False) is False


def test_transcode_worker_is_given_record_info():
    """轉碼任務要拿到主播資訊，否則鉤子不知道該不該轉寫。"""
    assert ("transcode_executor.submit(_transcode_worker, f, record_name, record_url, "
            "want_transcribe)") in MAIN_SRC
    assert "want_transcribe = bool(record_url and record_url in transcribe_urls)" in MAIN_SRC
    assert MAIN_SRC.count("submit_live_transcode(valid_ts_paths, record_name, record_url)") == 1
    assert MAIN_SRC.count("submit_live_transcode(completed_segments, record_name, record_url)") == 1
