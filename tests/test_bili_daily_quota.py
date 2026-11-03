# -*- coding: utf-8 -*-
"""回歸測試：B 站投稿必須按「滾動 24 小時最多 5 篇」限流。

只靠「最小投稿間隔 288 分鐘」是不夠的：288 × 5 剛好等於 24 小時，一點抖動
（前一筆晚幾分鐘開始、這筆早幾分鐘）就會擠進第 6 篇，直接吃 21566 風控並觸發
冷卻。所以 bili_slot_status 還要數一遍過去 24 小時真正提交過幾次。

main.py 是模組級無限迴圈、不能直接 import，這裡用 AST 取出函式本體，
在帶樁的全域命名空間裡執行，驗證真實邏輯而不是比對原始碼字串。
"""

import ast
import threading
import time
from pathlib import Path

MAIN_SRC = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")


def _load_slot_status():
    """把 bili_slot_status 抽出來，配上可注入的全域變數執行。"""
    tree = ast.parse(MAIN_SRC)
    node = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "bili_slot_status"
    )
    code = compile(ast.Module(body=[node], type_ignores=[]), "<bili_slot_status>", "exec")
    ns = {
        "time": time,
        "bili_submit_lock": threading.Lock(),
        "bili_cooldown_until": 0.0,
        "bili_last_submit_time": 0.0,
        "bili_min_interval_seconds": 0,
        "bili_submit_history": [],
        "BILI_DAILY_LIMIT": 5,
        "BILI_DAILY_WINDOW": 24 * 3600,
    }
    exec(code, ns)
    return ns


def test_quota_blocks_sixth_submission_inside_24h():
    """24 小時內已有 5 筆 → 必須拒絕，不能讓第 6 筆撞 21566。"""
    ns = _load_slot_status()
    now = time.time()
    # 均勻間隔 288 分鐘的 5 筆（最舊的 19.2 小時前），全落在 24 小時內
    ns["bili_submit_history"][:] = [now - 288 * 60 * (4 - i) for i in range(5)]

    allowed, reason, wait = ns["bili_slot_status"]()
    assert allowed is False
    assert "配额已满" in reason
    # 等最早那筆滑出 24 小時窗口即可
    oldest = min(ns["bili_submit_history"])
    assert abs(wait - (oldest + 24 * 3600 - now)) <= 2


def test_quota_allows_submission_when_oldest_entry_expires():
    """最早那筆滑出窗口後應立刻放行，而不是再等一個最小間隔。"""
    ns = _load_slot_status()
    now = time.time()
    ns["bili_submit_history"][:] = [now - 24 * 3600 - 10] + [
        now - 288 * 60 * (4 - i) for i in range(4)
    ]

    allowed, reason, wait = ns["bili_slot_status"]()
    assert allowed is True and wait == 0, reason


def test_quota_counts_only_last_24h():
    """超過 24 小時的舊紀錄不佔配額。"""
    ns = _load_slot_status()
    now = time.time()
    ns["bili_submit_history"][:] = [now - 30 * 3600 - i for i in range(10)]
    ns["bili_submit_history"].extend([now - 60 * 60 * i for i in range(4)])

    allowed, _, _ = ns["bili_slot_status"]()
    assert allowed is True


def test_submit_history_is_persisted_and_restored():
    """計數必須跨重啟保留，否則每次重啟就白送 5 個配額。"""
    save = MAIN_SRC[MAIN_SRC.index("def _save_bili_queue"):]
    save = save[:save.index("\ndef ")]
    assert "submit_history" in save

    load = MAIN_SRC[MAIN_SRC.index("def _load_bili_queue"):]
    load = load[:load.index("\ndef ")]
    assert "bili_submit_history" in load


def test_mark_bili_submitted_records_each_submission():
    i = MAIN_SRC.index("def mark_bili_submitted")
    body = MAIN_SRC[i:MAIN_SRC.index("def enqueue_bili_job")]
    assert "bili_submit_history.append(" in body
    assert "_save_bili_queue()" in body
