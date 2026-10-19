# -*- coding: utf-8 -*-
"""回歸測試：小紅書筆記的時間窗口過濾。

背景：新增博主時，監控會沿著該博主的歷史往回抓——實際發生過抓到 200 天前的舊帖
（2026-03-31 的筆記被下載）。原因是小紅書這條鏈路原本完全沒有日期判斷，只靠
history 去重，所以會一天 3 篇地慢慢回填整個陳年歷史。

關鍵在於 note_id 前 8 位十六進位就是建立時間戳（已與 15 篇已下載紀錄核對，
15/15 相符）。因此判斷新舊**不需要**抓詳情，跳過舊筆記是零成本的。
"""

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

# 真實 note_id 與其前 8 位十六進位解出的 Unix 時間戳
REAL_IDS = [
    ("6ad06ef70000000012036b4d", 1792044791),   # 2026-10-15
    ("6ad036a1000000001203e828", 1792030369),   # 2026-10-15
    ("6ac38a53000000001500d377", 1791199827),   # 2026-10-05
    ("69cbbf3f0000000023027691", 1774960447),   # 2026-03-31
]
SAMPLE_ID, SAMPLE_TS = REAL_IDS[0]


def test_note_id_timestamp_decodes_real_ids():
    """真實 note_id 的頭 8 位十六進位必須解出驗證過的建立時間。"""
    for nid, expected in REAL_IDS:
        assert xhs.note_id_timestamp(nid) == float(expected), nid


def test_note_id_timestamp_rejects_unparsable():
    """解不出日期時回傳 None，由呼叫方照常處理（寧可多抓，不可誤濾）。"""
    assert xhs.note_id_timestamp("") is None
    assert xhs.note_id_timestamp(None) is None
    assert xhs.note_id_timestamp("abc") is None              # 不足 8 位
    assert xhs.note_id_timestamp("zzzzzzzz0000000000000000") is None   # 非十六進位


def test_note_id_timestamp_rejects_implausible():
    """不合理時間視為格式變更 → None，避免小紅書改 ID 格式後把新筆記靜默濾掉。"""
    assert xhs.note_id_timestamp("0000000100000000abcd1234") is None   # 1970 年
    assert xhs.note_id_timestamp("ffffffff00000000abcd1234") is None   # 2106 年


def test_is_note_out_of_window_respects_boundary(monkeypatch):
    """36 小時窗口：1 小時後仍在窗內，37 小時後已出窗。"""
    monkeypatch.setattr(xhs.time, "time", lambda: SAMPLE_TS + 3600)
    assert xhs.is_note_out_of_window(SAMPLE_ID, 1.5) is False

    monkeypatch.setattr(xhs.time, "time", lambda: SAMPLE_TS + 37 * 3600)
    assert xhs.is_note_out_of_window(SAMPLE_ID, 1.5) is True


def test_is_note_out_of_window_disabled_when_non_positive():
    """--days 0（或 None）代表不限制，任何筆記都不算過期。"""
    assert xhs.is_note_out_of_window(SAMPLE_ID, 0) is False
    assert xhs.is_note_out_of_window(SAMPLE_ID, 0.0) is False
    assert xhs.is_note_out_of_window(SAMPLE_ID, None) is False


def test_is_note_out_of_window_keeps_unparsable_notes(monkeypatch):
    """note_id 解不出時間時必須保留（回傳 False），不可因格式問題靜默漏抓。"""
    monkeypatch.setattr(xhs.time, "time", lambda: SAMPLE_TS + 999 * 3600)
    assert xhs.is_note_out_of_window("zzzzzzzz0000000000000000", 1.5) is False


def test_retry_queue_round_trip(tmp_path):
    """重試清單必須可持久化並還原（跨巡檢保留 note_id → 失敗次數）。"""
    p = str(tmp_path / "retry.json")
    assert xhs.load_retry_queue(p) == {}

    xhs.save_retry_queue(p, {"note_a": 1, "note_b": 2})
    assert xhs.load_retry_queue(p) == {"note_a": 1, "note_b": 2}


def test_retry_queue_tolerates_corrupt_or_missing_file(tmp_path):
    """檔案損毀或不存在時應回空字典，不可讓整個巡檢崩潰。"""
    missing = str(tmp_path / "nope.json")
    assert xhs.load_retry_queue(missing) == {}

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert xhs.load_retry_queue(str(bad)) == {}


def test_order_notes_for_retry_puts_retries_first():
    """重試中的筆記排最前，其餘維持原本（新到舊）順序。"""
    notes = [{"note_id": "a"}, {"note_id": "b"}, {"note_id": "c"}]
    out = xhs.order_notes_for_retry(notes, {"c": 1})
    assert [n["note_id"] for n in out] == ["c", "a", "b"]


def test_retry_notes_bypass_window_decision():
    """重試中的筆記必須能繞過窗口判定——否則失敗一次後隔天就被永久跳過。"""
    assert xhs._XHS_MAX_DOWNLOAD_ATTEMPTS >= 2
    src = (ROOT / "xhs_note_monitor.py").read_text(encoding="utf-8")
    assert "if not retrying and is_note_out_of_window(note_id, since_days):" in src
