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
    i_cleanup = MAIN_SRC.index('handle_post_upload_cleanup(chunk["ts_paths"]')
    assert i_compute < i_cleanup, (
        "必須在上傳成功、依策略刪除 TS 之前算好分P起始時間，"
        "否則 ffprobe 讀不到被刪除的檔案，時間戳會全部變成 00:00"
    )


def test_timeline_is_built_after_upload_from_precomputed_start_times():
    """時間軸在所有分片處理完後才生成，且用先前算好的時間戳，不在執行緒內重新探測。"""
    i_cleanup = MAIN_SRC.index('handle_post_upload_cleanup(chunk["ts_paths"]')
    i_schedule = MAIN_SRC.index(
        'timeline["meta"], record_name, clean_name, timeline["starts"]'
    )
    assert i_schedule > i_cleanup, (
        "時間軸應在全部上傳結束後才生成，避免列出上傳失敗、B 站上不存在的分片"
    )
    # 不可再退回用整場 meta 直接排程（會與拆稿後重新編號的分P 標籤對不上）
    assert "schedule_bili_timeline(meta, record_name, clean_name, timeline_start_times)" not in MAIN_SRC
    assert "_build_successful_timeline(" in MAIN_SRC
    # 提前上傳的分段也要進時間軸，否則文件只覆蓋錄製結束時剩下的尾巴
    assert "timeline_prefix" in MAIN_SRC


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


def test_session_job_resolves_paths_from_disk():
    """投稿前要以入隊清單為準逐項解析（TS 被轉碼換掉就改指向同名 MP4），
    不能整場重新掃描——風控重投時清單只含未投完的分片。"""
    i_def = MAIN_SRC.index("def _resolve_session_video_paths")
    body = MAIN_SRC[i_def:MAIN_SRC.index("def run_bili_session_job")]
    assert 'job.get("ts_paths")' in body, "應以入隊時記下的清單為主"
    assert 'with_suffix(".mp4")' in body, "TS 被轉碼換掉後要改指向同名 MP4"
    # 重新掃描只能當清單整體失效時的退路
    assert body.index('with_suffix(".mp4")') < body.index("list_session_videos(")
    assert "_resolve_session_video_paths(job)" in MAIN_SRC[
        MAIN_SRC.index("def run_bili_session_job"):][:1200]


def test_final_segments_get_transcoded_when_queued():
    """场次结束时才 flush 的最后一段走不到录制循环的转码提交点，
    必须在入队后补提交转码，且要在 enqueue 之后。"""
    i_fn = MAIN_SRC.index("def process_session_multi_p_upload")
    body = MAIN_SRC[i_fn:MAIN_SRC.index("def transcribe_and_notify")]
    i_enqueue = body.index("enqueue_bili_job(job)")
    i_transcode = body.index("submit_live_transcode(valid_ts_paths)")
    assert i_enqueue < i_transcode
