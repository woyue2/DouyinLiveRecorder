# -*- coding: utf-8 -*-
"""回歸測試：檔名清洗函式徹底過濾 Emoji 與非法字元，防止百度網盤 31062 報錯。"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

xhs = _load("xhs_note_monitor", ROOT / "xhs_note_monitor.py")
douyin = _load("douyin_video_monitor", ROOT / "douyin_video_monitor.py")


def test_sanitize_removes_fire_emoji_and_special_chars():
    """真實案例：摩根士丹利首席_6个后市重要判断🔥 導致百度網盤 31062 報錯。"""
    raw_title = "摩根士丹利首席_6个后市重要判断🔥"
    res_xhs = xhs.sanitize_filename_part(raw_title, max_len=25)
    res_douyin = douyin.sanitize_filename_part(raw_title, max_len=25)

    assert "🔥" not in res_xhs
    assert "🔥" not in res_douyin
    assert res_xhs == "摩根士丹利首席_6个后市重要判断"
    assert res_douyin == "摩根士丹利首席_6个后市重要判断"


def test_sanitize_removes_various_emojis_and_symbols():
    cases = [
        ("💡【干货】台积电🔥涨停！🚀", "【干货】台积电_涨停"),
        ("✨投资随笔📌2026", "投资随笔_2026"),
        ("💯满分答卷💯", "满分答卷"),
        ("咖啡☕️与生活", "咖啡_与生活"),
    ]
    for raw, expected in cases:
        out = xhs.sanitize_filename_part(raw, max_len=25)
        # 不得包含常見 emoji
        for em in ("💡", "🔥", "🚀", "✨", "📌", "💯", "☕"):
            assert em not in out


def test_sanitize_handles_empty_and_illegal_fs_chars():
    assert xhs.sanitize_filename_part("") == ""
    assert xhs.sanitize_filename_part("   ") == ""
    # 特殊字元替換為底線，且頭尾不留底線
    res = xhs.sanitize_filename_part('a/b\\c:d*e?f"g<h>i|j')
    assert "/" not in res and "\\" not in res and ":" not in res
    assert "*" not in res and "?" not in res and '"' not in res
    assert "<" not in res and ">" not in res and "|" not in res


def test_sanitize_truncates_to_max_len():
    long_str = "一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十"
    assert len(xhs.sanitize_filename_part(long_str, max_len=10)) <= 10
    assert len(douyin.sanitize_filename_part(long_str, max_len=25)) <= 25
