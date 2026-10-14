# -*- coding: utf-8 -*-
"""回歸測試：直播時間軸必須在刪除本地 TS 之前算好分P時間戳。

主流程在上傳成功後會依策略刪除本地 TS（預設「仅百度云上传md(删除ts)」）。
若時間戳改成在背景執行緒裡才 ffprobe，檔案已不存在，所有分P起始時間
都會靜默退化成 00:00 —— 這正是本測試要守住的順序不變式。
"""

import ast
from pathlib import Path

MAIN_SRC = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")


def test_start_times_are_computed_before_ts_cleanup():
    i_compute = MAIN_SRC.index("compute_part_start_times(valid_ts_paths")
    i_schedule = MAIN_SRC.index("schedule_bili_timeline(meta, record_name, clean_name, start_times)")
    i_cleanup = MAIN_SRC.index(
        "handle_post_upload_cleanup(valid_ts_paths, policy=bili_post_policy)", i_schedule
    )
    assert i_compute < i_schedule < i_cleanup, (
        "必須先在清理本地 TS 之前算好分P起始時間，再排程生成時間軸文件，"
        "否則 ffprobe 讀不到被刪除的檔案，時間戳會全部變成 00:00"
    )


def test_timeline_doc_builder_does_not_probe_files_itself():
    """build_bili_timeline_doc 只接受算好的 start_times，不在執行緒內探測檔案。"""
    tree = ast.parse(MAIN_SRC)
    fn = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "build_bili_timeline_doc"
    )
    arg_names = [a.arg for a in fn.args.args]
    assert "start_times" in arg_names
    assert "ts_paths" not in arg_names

    body_src = ast.get_source_segment(MAIN_SRC, fn) or ""
    assert "probe_duration_seconds" not in body_src
    assert "compute_part_start_times" not in body_src


def test_schedule_bili_timeline_passes_start_times_through():
    """排程函式只負責背景生成，時間戳由呼叫方傳入而非自行探測。"""
    tree = ast.parse(MAIN_SRC)
    fn = next(
        n for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "schedule_bili_timeline"
    )
    arg_names = [a.arg for a in fn.args.args]
    assert "start_times" in arg_names
    body_src = ast.get_source_segment(MAIN_SRC, fn) or ""
    assert "probe_duration_seconds" not in body_src
