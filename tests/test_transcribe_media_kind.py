# -*- coding: utf-8 -*-
"""回歸測試：只保存 MP4 的場次也要能觸發轉寫。

原本 should_transcribe 只看「保存格式含純音頻」，於是 `MP4,转MD,传B站` 這種
配置永遠不會轉寫——沒有 md 就沒有分P看點、沒有 B 站簡介、飛書時間軸也是空的。
實際轉寫腳本用 `ffmpeg -i <輸入>` 流式解碼，MP4 本來就能直接吃，卡住的只是
main.py 的判斷。

同時要守住反面：TS|MP3 雙輸出必須只送 mp3，否則轉碼出來的 MP4 會被再送一遍，
同一段內容轉寫兩次，白燒 LLM 額度。
"""

import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
MAIN_SRC = (ROOT / "main.py").read_text(encoding="utf-8")

# 直接載入單一模組，繞過 src/__init__（會 import initializer → distro，Windows 上沒有）
_spec = importlib.util.spec_from_file_location("recording_config", ROOT / "src" / "recording_config.py")
_rc = importlib.util.module_from_spec(_spec)
sys.modules["recording_config"] = _rc  # dataclass 需要模組已註冊
_spec.loader.exec_module(_rc)
RecordFormat = _rc.RecordFormat


def _load(*names):
    tree = ast.parse(MAIN_SRC)
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(nodes) == len(names), [n.name for n in nodes]
    # 這些函式用到模組層的副檔名白名單，一併帶進來
    consts = [
        n for n in tree.body
        if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id.startswith("_TRANSCRIBE_") for t in n.targets
        )
    ]
    ns = {"RecordFormat": RecordFormat, "Path": Path}
    exec(compile(ast.Module(body=consts + nodes, type_ignores=[]), "<main>", "exec"), ns)
    return ns


def test_mp4_only_session_transcribes_video():
    ns = _load("transcribe_media_kind")
    assert ns["transcribe_media_kind"]("MP4") == "video"
    assert ns["transcribe_media_kind"]("MKV") == "video"
    assert ns["transcribe_media_kind"]("FLV") == "video"


def test_dual_output_still_uses_audio_only():
    """TS|MP3 若也把 MP4 送进去，同一段会被转写两次。"""
    ns = _load("transcribe_media_kind")
    assert ns["transcribe_media_kind"]("TS|MP3") == "audio"
    assert ns["transcribe_media_kind"]("MP3") == "audio"


def test_ts_only_and_empty_stay_untranscribed():
    """TS 場次沒有轉 MD 的習慣，且 TS 會被轉碼成 MP4 刪掉，維持不轉寫。"""
    ns = _load("transcribe_media_kind")
    assert ns["transcribe_media_kind"]("TS") == ""
    assert ns["transcribe_media_kind"]("") == ""


def test_video_extensions_need_explicit_permission():
    ns = _load("is_transcribable_audio")
    fn = ns["is_transcribable_audio"]
    assert fn("/x/a.mp3") is True
    assert fn("/x/a.mp4") is False, "預設不可放行，避免雙輸出場次重複轉寫"
    assert fn("/x/a.mp4", allow_video=True) is True
    assert fn("/x/a.ts", allow_video=True) is False


def test_should_transcribe_uses_media_kind():
    """錄製中與收播時兩條路徑都要把 allow_video 傳下去。"""
    i = MAIN_SRC.index("transcribe_kind = transcribe_media_kind(save_type)")
    assert "should_transcribe = bool(transcribe_kind)" in MAIN_SRC[i:i + 300]
    assert "transcribe_allow_video = transcribe_kind ==" in MAIN_SRC[i:i + 400]

    # 定義 1 + 錄製中/收播 2 + 轉碼後用 mp4 轉寫 1
    assert MAIN_SRC.count("submit_transcribe(") == 4
    assert MAIN_SRC.count("allow_video=transcribe_allow_video") == 2
    assert MAIN_SRC.count(", allow_video=True)") == 1  # 轉碼後用 mp4 轉寫那處
