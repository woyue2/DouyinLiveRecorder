# -*- coding: utf-8 -*-
"""回歸測試：直播時間軸必須在刪除本地 TS 之前算好分P時間戳。

主流程在上傳成功後會依策略刪除本地 TS（預設「仅百度云上传md(删除ts)」）。
若時間戳改成在背景執行緒裡才 ffprobe，檔案已不存在，所有分P起始時間
都會靜默退化成 00:00 —— 這正是本測試要守住的順序不變式。
"""

import ast
from pathlib import Path

MAIN_SRC = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")


def test_timeline_is_generated_at_session_end_not_after_upload():
    """時間軸在收播時就生成，不等 B 站投稿排隊（288 分鐘間隔會延遲數天）。"""
    i_fn = MAIN_SRC.index("def process_session_multi_p_upload")
    body = MAIN_SRC[i_fn:MAIN_SRC.index("def transcribe_and_notify")]
    assert "schedule_session_timeline(" in body, "收播流程應直接生成時間軸"
    # 必須早於投稿動作，不能退回「投完才生成」
    assert body.index("schedule_session_timeline(") < body.index("run_bili_job(job)")


def test_start_times_probed_before_files_could_be_cleaned():
    """時間軸靠 ffprobe 讀檔，必須先算好再排程。"""
    i_fn = MAIN_SRC.index("def process_session_multi_p_upload")
    body = MAIN_SRC[i_fn:MAIN_SRC.index("def transcribe_and_notify")]
    assert body.index("schedule_session_timeline(") > 0
    # 起始時間在背景 worker 裡、建文檔之前才算（那时檔案还在）
    i_worker = MAIN_SRC.index("def schedule_session_timeline(")
    wbody = MAIN_SRC[i_worker:MAIN_SRC.index("def run_bili_single_job(")]
    assert wbody.index("compute_part_start_times(") < wbody.index("build_bili_timeline_doc(")


def test_upload_path_does_not_build_timeline():
    """投稿端不再生成時間軸，否則同一場會建出第二份文檔。"""
    i_fn = MAIN_SRC.index("def run_bili_session_job")
    body = MAIN_SRC[i_fn:MAIN_SRC.index("def upload_live_batch")]
    assert "schedule_bili_timeline(" not in body


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


def test_session_job_waits_for_transcode_before_resolving_paths():
    """轉碼完成會刪原 TS；上傳前必須等該場次的轉碼收尾，否則 biliup 讀到一半檔案就沒了。"""
    i_def = MAIN_SRC.index("def run_bili_session_job")
    body = MAIN_SRC[i_def:i_def + 900]
    i_wait = body.index("_wait_transcode_drain(session_dir)")
    i_resolve = body.index("_resolve_session_video_paths(job)")
    assert i_wait < i_resolve


def test_timeline_doc_is_remembered_then_backfilled_after_upload():
    """收播建好文档要记下 doc_token，投稿成功后再把 BV 号补进同一篇。"""
    assert "_remember_timeline_doc(" in MAIN_SRC, "收播生成后应记录文档"
    i_fn = MAIN_SRC.index("def run_bili_session_job")
    body = MAIN_SRC[i_fn:MAIN_SRC.index("def upload_live_batch")]
    assert "_unpublished_bvids(session_dir" in body, "只补本次新增的分稿"
    assert "append_text_block(" in body, "应把 BV 号追加进文档"
    assert "_drop_timeline_doc(session_dir)" in body, "整场投完才移除记录"
    assert "_mark_published(" in body, "追加成功才记帐"
    # 补写失败不能影响投稿结果
    assert "回填时间轴文档失败" in body
