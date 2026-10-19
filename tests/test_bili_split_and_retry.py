# -*- coding: utf-8 -*-
"""回歸測試：B 站投稿的拆稿門檻與失敗重投。

main.py 無法直接 import（匯入即進入錄製主迴圈），所以沿用原始碼層級的不變式檢查。
"""

import ast
from pathlib import Path

MAIN_SRC = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")


def _session_job_body() -> str:
    tree = ast.parse(MAIN_SRC)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "run_bili_session_job"
    )
    return ast.get_source_segment(MAIN_SRC, fn) or ""


def test_split_threshold_is_by_size_not_part_count():
    """拆稿門檻必須是體積：瓶頸是「上行帶寬 × biliup 2 小時超時」，不是分段數。

    用分段數當門檻在改畫質後會立刻失準——同樣 8 段，高清的體積可能是流暢的數倍，
    照樣超時。此測試守住「門檻是 GB」這一事實，避免被改回去。
    """
    assert "单稿最大体积(GB)" in MAIN_SRC
    assert "单稿最大分P数" not in MAIN_SRC


def test_chunk_session_meta_receives_byte_budget():
    """呼叫切分時必須傳入以位元組為單位的預算，而非分段數。"""
    body = _session_job_body()
    assert "chunk_session_meta(meta, valid_ts_paths, bili_max_chunk_bytes)" in body


def test_failed_chunk_retries_once_but_never_on_rate_limit():
    """非風控失敗自動重投一次；風控失敗必須直接進冷卻，硬重試只會加重風控。"""
    body = _session_job_body()
    assert body.count("_upload_one_chunk(chunk, is_only_self)") == 2, (
        "首次上傳 + 重投一次，run_bili_session_job 內應恰好呼叫 _upload_one_chunk 兩次"
    )
    assert "not success and _bili_upload_retryable(output)" in body, (
        "重投條件必須排除風控失敗、上傳超時與檔案不存在等不可重投的情況"
    )


def test_non_retryable_markers_cover_rate_limit_and_timeout():
    """風控與超時都不可重投：前者會加重風控，後者可能在 B 站端已建稿而產生重複。"""
    for marker in ("RATE_LIMITED_21566", "上传超时", "视频文件不存在", "Cookie 文件不存在"):
        assert marker in MAIN_SRC, f"不可重投標記缺少: {marker}"
    assert "def _bili_upload_retryable(" in MAIN_SRC


def test_retry_only_resends_chunk_whose_ts_were_not_cleaned():
    """重投依賴本地 TS 仍在：清理只可在成功分支執行，否則重投會沒東西可傳。"""
    body = _session_job_body()
    i_cleanup = body.index("handle_post_upload_cleanup(chunk[\"ts_paths\"]")
    i_success = body.index("if success:")
    assert i_success < i_cleanup, "清理必須放在成功分支內"
