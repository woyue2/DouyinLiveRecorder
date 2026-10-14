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
    i_schedule = MAIN_SRC.index(
        "schedule_bili_timeline(meta, record_name, clean_name, timeline_start_times)"
    )
    i_cleanup = MAIN_SRC.index("handle_post_upload_cleanup(", i_schedule)
    assert i_compute < i_schedule < i_cleanup, (
        "必須先在清理本地 TS 之前算好分P起始時間，再排程生成時間軸文件，"
        "否則 ffprobe 讀不到被刪除的檔案，時間戳會全部變成 00:00"
    )


def test_live_transcription_archives_to_timeline_not_works_library():
    """直播轉寫必須歸入「直播時間軸」，不可混進抖音/小紅書作品的「主播文檔庫」。"""
    tree = ast.parse(MAIN_SRC)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "transcribe_and_notify"
    )
    body = ast.get_source_segment(MAIN_SRC, fn) or ""
    assert "wiki_root_node=bili_timeline_node" in body, (
        "直播轉寫的飛書歸檔根節點應為 bili_timeline_node（直播時間軸）"
    )
    # 主播節點名要去掉 URL_config.ini 的「序号N」前綴
    assert "display_name" in body and "_resolve_streamer_type" in body
    # 標題要用 AI 提煉的 H1，而不是毫無辨識度的原始檔名
    assert "extract_article_title" in body, (
        "直播轉寫文檔標題應取 Markdown 的 H1（AI 提煉標題），不可直接用檔名"
    )


def test_work_monitors_archive_to_works_library():
    """抖音作品與小紅書筆記應歸入「主播文檔庫」(feishu_wiki_root_node)。"""
    for path in ("douyin_video_monitor.py", "xhs_note_monitor.py"):
        src = (Path(__file__).parents[1] / path).read_text(encoding="utf-8")
        assert "wiki_root_node=config_settings.get(\"feishu_wiki_root_node\"" in src, (
            f"{path} 應以 feishu_wiki_root_node（主播文檔庫）作為歸檔根節點"
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
