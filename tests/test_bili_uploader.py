# -*- coding: utf-8 -*-
import importlib.util
import os
import sys
import tempfile
from pathlib import Path


def load_module(name: str, file_name: str):
    module_path = Path(__file__).parents[1] / "src" / file_name
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bili_uploader = load_module("bili_uploader", "bili_uploader.py")
extract_bili_metadata = bili_uploader.extract_bili_metadata
handle_post_upload_cleanup = bili_uploader.handle_post_upload_cleanup


def test_extract_bili_metadata_explicit_meta():
    md_content = """# 2026-10-05 直播记录
<!--BILI_META_START-->
TITLE: 乐乐当场笑崩！这名场面谁顶得住啊
TAGS: 合家欢乐乐乐,搞笑,名场面,整蛊
<!--BILI_META_END-->

<!--SUMMARY_START-->
本段精彩看点：
- 乐乐突发整蛊，直播间当场笑崩
- 直播间地址 https://live.douyin.com/259728108048 欢迎常来
<!--SUMMARY_END-->
"""
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(md_content)
        temp_md = f.name

    try:
        meta = extract_bili_metadata(temp_md, "主播: 合家欢乐乐乐", streamer_type="娱乐搞笑")
        # 验证标题
        assert meta["title"] == "乐乐当场笑崩！这名场面谁顶得住啊"
        assert "【直播精华】" not in meta["title"]
        # 验证简介中清除了原直播链接
        assert "https://live.douyin.com" not in meta["desc"]
        assert "当场笑崩" in meta["desc"]
        # 验证标签
        assert "合家欢乐乐乐" in meta["tags"]
        assert "名场面" in meta["tags"]
    finally:
        os.remove(temp_md)


def test_extract_bili_metadata_fallback_from_summary():
    # 模拟只有旧式 SUMMARY 的情况
    md_content = """<!--SUMMARY_START-->
> 精彩看点：
> - 05:20 乐乐模仿榜一大哥说话，节目效果直接拉满
> - 12:40 突发搞笑反转
<!--SUMMARY_END-->
"""
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(md_content)
        temp_md = f.name

    try:
        meta = extract_bili_metadata(temp_md, "合家欢乐乐乐", streamer_type="娱乐搞笑")
        # 自动提炼看点首行，带主播名前缀，不带【直播精华】
        assert meta["title"].startswith("合家欢乐乐乐：")
        assert "节目效果直接拉满" in meta["title"]
        assert "【直播精华】" not in meta["title"]
        assert "合家欢乐乐乐" in meta["tags"]
        assert "搞笑" in meta["tags"]
    finally:
        os.remove(temp_md)


def test_cleanup_policy():
    with tempfile.NamedTemporaryFile("w", suffix=".ts", delete=False) as f:
        f.write("fake video")
        temp_ts = f.name

    placeholder = temp_ts + ".bili_uploading"
    with open(placeholder, "w") as f:
        f.write("1")

    assert os.path.exists(temp_ts)
    assert os.path.exists(placeholder)

    # 策略：上传B站后删除本地 TS
    handle_post_upload_cleanup(temp_ts, policy="仅百度云上传md(删除ts)")

    assert not os.path.exists(temp_ts)
    assert not os.path.exists(placeholder)


def test_parse_url_config_multi_flags():
    # 测试主程序中支持多个额外标签
    # 模拟 main.py 中的 parse_url_config_line 逻辑
    import csv
    def parse_line(line, default_quality="原画"):
        if ',' in line:
            split_line = next(csv.reader([line], skipinitialspace=True))
        else:
            split_line = [line, '']
        split_line = [i.strip() for i in split_line]
        if 'http' in split_line[0]:
            quality = default_quality
            url, name, save_type = split_line[0], split_line[1], split_line[2]
            extra_flags = split_line[3:]
        else:
            quality, url, name = split_line[:3]
            save_type = split_line[3] if len(split_line) > 3 else ''
            extra_flags = split_line[4:]
        return quality, url, name, save_type, extra_flags

    line1 = "原画,https://live.douyin.com/259728108048,主播: 合家欢乐乐乐,TS|MP3,转MD,传B站"
    q, u, n, s, flags = parse_line(line1)
    assert '转MD' in flags
    assert '传B站' in flags
    assert s == 'TS|MP3'

    line2 = "https://live.douyin.com/259728108048,主播: 合家欢乐乐乐,TS|MP3,传B站,转MD"
    q, u, n, s, flags = parse_line(line2)
    assert '转MD' in flags
    assert '传B站' in flags


def test_cleanup_policy_keep_local():
    with tempfile.NamedTemporaryFile("w", suffix=".ts", delete=False) as f:
        f.write("fake video")
        temp_ts = f.name

    placeholder = temp_ts + ".bili_uploading"
    with open(placeholder, "w") as f:
        f.write("1")

    # 策略：保留本地
    handle_post_upload_cleanup(temp_ts, policy="保留本地")

    assert os.path.exists(temp_ts)
    assert not os.path.exists(placeholder)
    os.remove(temp_ts)


def test_tag_length_and_limit():
    md_content = """<!--BILI_META_START-->
TITLE: 乐乐搞笑测试
TAGS: 合家欢乐乐乐,超长超长超长超长超长超长超长超长超长超长标签,短标签1,短标签2,短标签3,短标签4,短标签5,短标签6,短标签7,短标签8,短标签9,短标签10,短标签11,短标签12
<!--BILI_META_END-->
"""
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(md_content)
        temp_md = f.name

    try:
        meta = extract_bili_metadata(temp_md, "合家欢乐乐乐", streamer_type="娱乐搞笑")
        tags = meta["tags"].split(",")
        # 标签最多 12 个
        assert len(tags) <= 12
        # 单个标签不超过 20 字符
        for t in tags:
            assert len(t) <= 20
        # 主播名在首位
        assert tags[0] == "合家欢乐乐乐"
    finally:
        os.remove(temp_md)


def test_upload_is_only_self(monkeypatch):
    import subprocess
    recorded_cmd = []

    def fake_run(cmd, *args, **kwargs):
        nonlocal recorded_cmd
        recorded_cmd = cmd
        class FakeResult:
            returncode = 0
            stdout = "bvid: String(\"BVtest123\")"
            stderr = ""
        return FakeResult()

    monkeypatch.setattr(subprocess, "run", fake_run)

    with tempfile.NamedTemporaryFile("w", suffix=".ts", delete=False) as f:
        f.write("fake video")
        temp_ts = f.name
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        f.write("{}")
        temp_cookie = f.name

    try:
        # 测试 is_only_self=True
        success, out = bili_uploader.upload_video_biliup(
            video_path=temp_ts,
            title="测试标题",
            desc="测试简介",
            tags="测试标签",
            cookie_path=temp_cookie,
            is_only_self=True
        )
        assert success
        assert "--is-only-self" in recorded_cmd
        idx = recorded_cmd.index("--is-only-self")
        assert recorded_cmd[idx + 1] == "1"

        # 测试 is_only_self=False (公开)
        success, out = bili_uploader.upload_video_biliup(
            video_path=temp_ts,
            title="测试标题",
            desc="测试简介",
            tags="测试标签",
            cookie_path=temp_cookie,
            is_only_self=False
        )
        assert success
        assert "--is-only-self" not in recorded_cmd
    finally:
        os.remove(temp_ts)
        os.remove(temp_cookie)


def test_multi_chunk_aborts_when_first_comment_fails(monkeypatch):
    """首条评论发送失败时，必须立即中止后续评论，避免无效连发。"""
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda _s: None)

    posted = []
    monkeypatch.setattr(
        bili_uploader, "post_video_comment",
        lambda **kw: (posted.append(kw["message"]), (False, "发评论失败 code=12025"))[1],
    )
    monkeypatch.setattr(bili_uploader, "comment_exists", lambda *a, **k: True)

    results = bili_uploader.post_multi_chunk_comments(
        "BVtest", ["c1", "c2", "c3"], "cookies.json",
        aid=1, delay_between=(180, 360), verify_first=True,
    )
    assert len(results) == 1
    assert results[0]["ok"] is False
    assert posted == ["c1"]


def test_multi_chunk_aborts_when_first_comment_not_visible(monkeypatch):
    """首条接口返回成功但评论区查不到时，同样必须中止后续评论。"""
    import time as _time
    monkeypatch.setattr(_time, "sleep", lambda _s: None)

    posted = []
    monkeypatch.setattr(
        bili_uploader, "post_video_comment",
        lambda **kw: (posted.append(kw["message"]), (True, "3169"))[1],
    )
    monkeypatch.setattr(bili_uploader, "comment_exists", lambda *a, **k: False)

    results = bili_uploader.post_multi_chunk_comments(
        "BVtest", ["c1", "c2", "c3"], "cookies.json",
        aid=1, delay_between=(180, 360), verify_first=True,
    )
    assert len(results) == 1
    assert results[0]["ok"] is True
    assert posted == ["c1"]


def test_multi_chunk_continues_with_random_gap_after_verified_first(monkeypatch):
    """首条确认存在后继续发送，且后续间隔落在指定的随机区间内。"""
    import time as _time
    sleeps = []
    monkeypatch.setattr(_time, "sleep", lambda s: sleeps.append(s))

    monkeypatch.setattr(bili_uploader, "post_video_comment", lambda **kw: (True, "3169"))
    monkeypatch.setattr(bili_uploader, "comment_exists", lambda *a, **k: True)

    results = bili_uploader.post_multi_chunk_comments(
        "BVtest", ["c1", "c2", "c3"], "cookies.json",
        aid=1, delay_between=(180, 360), verify_first=True, verify_settle=15,
    )
    assert len(results) == 3
    assert all(r["ok"] for r in results)

    # sleeps[0] 是首条校验前的 settle，其余为两条评论之间的随机间隔
    assert sleeps[0] == 15
    gaps = sleeps[1:]
    assert len(gaps) == 2
    for gap in gaps:
        assert 180 <= gap <= 360


def test_multi_chunk_fixed_delay_still_supported(monkeypatch):
    """delay_between 传 int 时维持固定间隔（向下兼容）。"""
    import time as _time
    sleeps = []
    monkeypatch.setattr(_time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(bili_uploader, "post_video_comment", lambda **kw: (True, "3169"))
    monkeypatch.setattr(bili_uploader, "comment_exists", lambda *a, **k: True)

    bili_uploader.post_multi_chunk_comments(
        "BVtest", ["c1", "c2"], "cookies.json",
        aid=1, delay_between=30, verify_first=False,
    )
    assert sleeps == [30]


def test_build_parts_timeline_markdown_includes_all_parts_and_timestamps():
    """时间轴文档：不截断、含主播/日期/段数表头，并按分P标注起始时间戳。"""
    parts = [
        {"label": "【P1】", "title": "P1 开头", "lines": ["00:30 看点A", "12:40 看点B"]},
        {"label": "【P2】", "title": "P2 中段", "lines": ["看点C"]},
    ]
    md = bili_uploader.build_parts_timeline_markdown(
        parts, record_name="序号6 初恋小娜", upload_date="2026-10-14",
        start_times=[0, 901],
    )
    assert md.startswith("# 直播时间轴 · 初恋小娜")
    assert "📅 2026-10-14" in md
    assert "🎬 共 2 段" in md
    # 分P 標題去掉了 "P1" 前缀，并带上该分P的全域起始时间戳
    assert "## 【P1】 00:00 · 开头" in md
    assert "## 【P2】 15:01 · 中段" in md
    # 所有看点都保留（无字数截断）
    for point in ("00:30 看点A", "12:40 看点B", "看点C"):
        assert point in md


def test_build_parts_timeline_markdown_handles_empty_and_missing_titles():
    parts = [
        {"label": "【P1】", "title": "", "lines": []},
        {"label": "", "title": "仅标题", "lines": ["只有一行"]},
    ]
    md = bili_uploader.build_parts_timeline_markdown(parts, record_name="主播: 小娜")
    assert "## 【P1】" in md
    assert "（本段无转写内容）" in md
    assert "## 【P2】 仅标题" in md
    assert "- 只有一行" in md


def test_probe_duration_and_start_times_fall_back(monkeypatch):
    """ffprobe 取不到时长时，用 fallback 秒数累加，保证时间戳仍递增。"""
    monkeypatch.setattr(bili_uploader, "probe_duration_seconds", lambda p: 0.0)
    starts = bili_uploader.compute_part_start_times(["a", "b", "c"], fallback_seconds=900)
    assert starts == [0, 900, 1800]

    monkeypatch.setattr(bili_uploader, "probe_duration_seconds", lambda p: 901.0)
    starts = bili_uploader.compute_part_start_times(["a", "b"])
    assert starts == [0, 901]


def test_format_timestamp():
    assert bili_uploader.format_timestamp(0) == "00:00"
    assert bili_uploader.format_timestamp(901) == "15:01"
    assert bili_uploader.format_timestamp(3725) == "01:02:05"


def test_build_parts_index_chunks():
    # 测试较少 P 时单条
    short_parts = [
        {"label": "【P1】", "lines": ["01:30 搞笑名场面1", "03:45 搞笑名场面2"]},
        {"label": "【P2】", "lines": ["02:10 搞怪PK局", "05:00 表情包截屏"]},
    ]
    chunks1 = bili_uploader.build_parts_index_chunks(short_parts, max_len_per_chunk=900)
    assert len(chunks1) == 1
    assert "【P1】" in chunks1[0]
    assert "【P2】" in chunks1[0]
    assert len(chunks1[0]) <= 900

    # 测试较多 P 时自动切分为多条（每条 <= 900 字）
    long_parts = [
        {"label": f"【P{i}】", "lines": [f"0{i}:15 核心观点第{i}段详细解析，方法论与启发", f"0{i}:45 补充案例第{i}段金句说明"]}
        for i in range(1, 15)
    ]
    chunks2 = bili_uploader.build_parts_index_chunks(long_parts, max_len_per_chunk=400)
    assert len(chunks2) >= 2
    for c in chunks2:
        assert len(c) <= 400
        assert "📌 分P看点索引" in c




def _mk_session_meta(n: int) -> dict:
    return {
        "title": "整場高能合集",
        "desc": "whole desc",
        "tags": "主播,搞笑",
        "parts": [
            {"label": f"【P{i + 1}】", "lines": [f"0{i}:15 看點{i + 1}"], "title": f"P{i + 1} 看點{i + 1}"}
            for i in range(n)
        ],
        "part_descs": [f"第{i + 1}段簡介" for i in range(n)],
    }


def _mk_ts_files(tmp_path, sizes: list) -> list:
    """建立指定大小的真實 .ts 檔（chunk_session_meta 以 os.path.getsize 判斷體積）。"""
    paths = []
    for i, size in enumerate(sizes):
        p = tmp_path / f"{i}.ts"
        p.write_bytes(b"x" * size)
        paths.append(str(p))
    return paths


def test_chunk_session_meta_splits_by_total_size(tmp_path):
    """體積超過單稿上限時切成多份，標題加 1/2/3 後綴；TS 依序切分不重不漏。"""
    ts = _mk_ts_files(tmp_path, [100] * 7)          # 共 700
    chunks = bili_uploader.chunk_session_meta(_mk_session_meta(7), ts, 250)
    assert [len(c["ts_paths"]) for c in chunks] == [2, 2, 2, 1]
    assert [c["total"] for c in chunks] == [4, 4, 4, 4]
    assert [c["title"] for c in chunks] == [
        "整場高能合集 1", "整場高能合集 2", "整場高能合集 3", "整場高能合集 4",
    ]
    assert [p for c in chunks for p in c["ts_paths"]] == ts


def test_chunk_session_meta_renumbers_parts_within_each_chunk(tmp_path):
    """第 2 份的分P必須從 P1 重新編號，否則播放器列表會從 P3 起跳。"""
    ts = _mk_ts_files(tmp_path, [100] * 7)
    chunks = bili_uploader.chunk_session_meta(_mk_session_meta(7), ts, 250)
    second = chunks[1]
    assert second["parts"][0]["label"] == "【P1】"
    assert second["parts"][0]["title"].startswith("P1 ")
    assert second["parts"][-1]["label"] == "【P2】"
    assert second["parts"][0]["lines"] == ["02:15 看點3"]


def test_chunk_session_meta_desc_only_covers_own_parts(tmp_path):
    """每份的簡介只能涵蓋自己那幾個分P，不可照抄整場簡介。"""
    ts = _mk_ts_files(tmp_path, [100] * 7)
    chunks = bili_uploader.chunk_session_meta(_mk_session_meta(7), ts, 250)
    assert "第1段簡介" in chunks[0]["desc"]
    assert "第3段簡介" not in chunks[0]["desc"]
    assert "第3段簡介" in chunks[1]["desc"]
    assert "第1段簡介" not in chunks[1]["desc"]


def test_chunk_session_meta_single_oversized_part_forms_own_chunk(tmp_path):
    """單段自身就超過上限時，仍必須自成一份（不可切成空稿件）。"""
    ts = _mk_ts_files(tmp_path, [500, 100, 100])
    chunks = bili_uploader.chunk_session_meta(_mk_session_meta(3), ts, 250)
    assert [len(c["ts_paths"]) for c in chunks] == [1, 2]


def test_chunk_session_meta_no_split_when_under_limit(tmp_path):
    """未超過上限時維持單稿件原貌，標題不加後綴。"""
    ts = _mk_ts_files(tmp_path, [100, 100])
    chunks = bili_uploader.chunk_session_meta(_mk_session_meta(2), ts, 250)
    assert len(chunks) == 1
    assert chunks[0]["title"] == "整場高能合集"
    assert chunks[0]["desc"] == "whole desc"
    assert chunks[0]["ts_paths"] == ts


def test_chunk_session_meta_zero_limit_disables_splitting(tmp_path):
    ts = _mk_ts_files(tmp_path, [100] * 10)
    chunks = bili_uploader.chunk_session_meta(_mk_session_meta(10), ts, 0)
    assert len(chunks) == 1
    assert chunks[0]["title"] == "整場高能合集"


def test_chunk_session_meta_missing_file_counts_as_zero(tmp_path):
    """檔案不存在時體積視為 0，不應拋錯中斷整個投稿流程。"""
    ts = _mk_ts_files(tmp_path, [100, 100]) + [str(tmp_path / "nope.ts")]
    chunks = bili_uploader.chunk_session_meta(_mk_session_meta(3), ts, 250)
    assert [p for c in chunks for p in c["ts_paths"]] == ts


def test_merge_queue_job_appends_new_session():
    q = [{"session_dir": "/a", "ts_paths": ["a1.ts"]}]
    pos, deduped = bili_uploader.merge_queue_job(q, {"session_dir": "/b", "ts_paths": ["b1.ts"]})
    assert (pos, deduped) == (2, False)
    assert len(q) == 2


def test_merge_queue_job_dedupes_same_session_and_updates_ts():
    """斷流讓同一場次被重複入隊時，應原地更新 TS 清單，不新增一筆。"""
    q = [
        {"session_dir": "/a", "ts_paths": ["a1.ts"]},
        {"session_dir": "/b", "ts_paths": ["b1.ts", "b2.ts"]},
    ]
    pos, deduped = bili_uploader.merge_queue_job(
        q, {"session_dir": "/b", "ts_paths": ["b1.ts", "b2.ts", "b3.ts"], "reason": "最小投稿间隔"}
    )
    assert (pos, deduped) == (2, True)
    assert len(q) == 2, "不應新增佇列項"
    assert q[1]["ts_paths"] == ["b1.ts", "b2.ts", "b3.ts"], "應更新為最新 TS 清單"
    assert "reason" not in q[1], "舊的等待原因應被清掉，避免沿用過期的等待秒數"


def test_merge_queue_job_keeps_original_position_on_dedupe():
    """去重時必須保持原本排位，不可被挪到隊尾。"""
    q = [
        {"session_dir": "/a", "ts_paths": ["a1.ts"]},
        {"session_dir": "/b", "ts_paths": ["b1.ts"]},
        {"session_dir": "/c", "ts_paths": ["c1.ts"]},
    ]
    pos, _ = bili_uploader.merge_queue_job(q, {"session_dir": "/a", "ts_paths": ["a1.ts", "a2.ts"]})
    assert pos == 1
    assert [j["session_dir"] for j in q] == ["/a", "/b", "/c"]


_MB = 1024 * 1024


def _mk_ts_sized(dir_path, sizes):
    """建立指定大小的 .ts（sparse，速度快）。"""
    out = []
    for i, n in enumerate(sizes):
        p = dir_path / f"主播_2026-10-19_00-00-00_{i:03d}.ts"
        with open(p, "wb") as f:
            f.truncate(n)
        out.append(str(p))
    return out


def test_pick_live_batch_waits_until_80_percent(tmp_path):
    """未累積到門檻 8 成時不出手，避免把一次投稿拆成好幾次。"""
    _mk_ts_sized(tmp_path, [2 * _MB] * 3)
    assert bili_uploader.pick_live_batch(str(tmp_path), 10 * _MB, min_age_seconds=0) == []


def test_pick_live_batch_returns_full_batch(tmp_path):
    _mk_ts_sized(tmp_path, [2 * _MB] * 4)
    batch = bili_uploader.pick_live_batch(str(tmp_path), 10 * _MB, min_age_seconds=0)
    assert len(batch) == 4


def test_pick_live_batch_respects_max_bytes(tmp_path):
    _mk_ts_sized(tmp_path, [2 * _MB] * 3)
    batch = bili_uploader.pick_live_batch(str(tmp_path), 5 * _MB, min_age_seconds=0)
    assert len(batch) == 2


def test_pick_live_batch_skips_fresh_segments(tmp_path):
    """剛完成的分段先別傳，給轉寫留時間；年齡門檻未到就整批不取。"""
    _mk_ts_sized(tmp_path, [2 * _MB] * 4)
    assert bili_uploader.pick_live_batch(str(tmp_path), 10 * _MB, min_age_seconds=600) == []


def test_pick_live_batch_disabled_when_limit_is_zero(tmp_path):
    _mk_ts_sized(tmp_path, [2 * _MB] * 4)
    assert bili_uploader.pick_live_batch(str(tmp_path), 0, min_age_seconds=0) == []


def test_live_ledger_round_trip(tmp_path):
    """已上傳帳本要能持久化，且 elapsed 供時間軸接續。"""
    bili_uploader.mark_segments_uploaded(
        str(tmp_path), {"a_000.ts": {"start": 0, "label": "【P1】"}}, elapsed=900
    )
    assert bili_uploader.uploaded_segment_names(str(tmp_path)) == {"a_000.ts"}
    assert bili_uploader.load_live_ledger(str(tmp_path))["elapsed"] == 900


def test_pick_live_batch_excludes_already_uploaded(tmp_path):
    """是否上傳過以帳本為準，不能只靠「TS 還在不在」。"""
    paths = _mk_ts_sized(tmp_path, [2 * _MB] * 4)
    names = [os.path.basename(p) for p in paths]
    batch = bili_uploader.pick_live_batch(
        str(tmp_path), 4 * _MB, exclude={names[0]}, min_age_seconds=0
    )
    assert [os.path.basename(p) for p in batch] == names[1:3]


def test_pick_live_batch_single_large_segment_still_uploads(tmp_path):
    """單段就超過 8 成門檻時仍要出手，否則高碼率下這個功能永遠不觸發。"""
    _mk_ts_sized(tmp_path, [3 * _MB] * 2)
    batch = bili_uploader.pick_live_batch(str(tmp_path), 4 * _MB, min_age_seconds=0)
    assert len(batch) == 1


def test_list_session_videos_includes_mp4_and_ts_excludes_part(tmp_path):
    """逐段轉碼後分段會變成 .mp4，尚未轉的仍是 .ts，兩者都要算；正在寫的 .part 不算。"""
    for name, size in [("a_000.ts", 3 * _MB), ("a_001.mp4", 3 * _MB), ("a_002.ts.part", 5 * _MB)]:
        with open(tmp_path / name, "wb") as f:
            f.truncate(size)
    with open(tmp_path / "tiny.ts", "wb") as f:
        f.truncate(1024)          # 小於 2MB，忽略

    names = sorted(p.name for p in bili_uploader.list_session_videos(str(tmp_path)))
    assert names == ["a_000.ts", "a_001.mp4"]


def test_list_session_videos_prefers_mp4_when_both_exist(tmp_path):
    """轉碼完成寫入 MP4 後才刪 TS，這段空窗兩者並存；同一段只能算一次，取 MP4。"""
    for name in ["a_000.ts", "a_000.mp4", "a_001.ts"]:
        with open(tmp_path / name, "wb") as f:
            f.truncate(3 * _MB)

    names = sorted(p.name for p in bili_uploader.list_session_videos(str(tmp_path)))
    assert names == ["a_000.mp4", "a_001.ts"]


def test_pick_live_batch_excludes_by_stem_after_transcode_rename(tmp_path):
    """帳本記的是 a_000.ts，但該分段已被轉碼成 a_000.mp4；用 stem 比對才不會重傳。"""
    import os
    import time
    for name in ["a_000.mp4", "a_001.mp4"]:
        p = tmp_path / name
        with open(p, "wb") as f:
            f.truncate(3 * _MB)
        os.utime(p, (time.time() - 600, time.time() - 600))

    batch = bili_uploader.pick_live_batch(
        str(tmp_path), max_bytes=3 * _MB, exclude={"a_000.ts"}, min_age_seconds=0
    )
    assert [p.name for p in map(__import__("pathlib").Path, batch)] == ["a_001.mp4"]


def test_list_session_videos_ignores_other_containers(tmp_path):
    """.flv/.mkv 不在納入範圍：原本各處只 glob *.ts，收進來會讓整場轉檔變額外分P。"""
    for name in ["a_000.ts", "a_001.flv", "a_002.mkv"]:
        with open(tmp_path / name, "wb") as f:
            f.truncate(3 * _MB)

    names = sorted(p.name for p in bili_uploader.list_session_videos(str(tmp_path)))
    assert names == ["a_000.ts"]


def test_pick_live_batch_skips_segments_still_transcoding(tmp_path):
    """還在轉碼的分段不能進批次：轉完會刪原 TS，上傳到一半檔案就消失了。"""
    import os
    import time
    for name in ["a_000.ts", "a_001.ts"]:
        p = tmp_path / name
        with open(p, "wb") as f:
            f.truncate(3 * _MB)
        os.utime(p, (time.time() - 600, time.time() - 600))

    busy = str(tmp_path / "a_000.ts")
    batch = bili_uploader.pick_live_batch(
        str(tmp_path), max_bytes=3 * _MB, min_age_seconds=0, skip={busy}
    )
    assert [p.name for p in map(__import__("pathlib").Path, batch)] == ["a_001.ts"]


def test_release_small_placeholders_unlocks_fragments(tmp_path):
    """<2MB 的碎片永远进不了投稿队列，占位要解除让百度云接手；
    >=2MB 的还要投稿，占位必须留着。"""
    big = tmp_path / "a_000.mp4"
    small = tmp_path / "a_001.mp4"
    with open(big, "wb") as f:
        f.truncate(3 * _MB)
    with open(small, "wb") as f:
        f.truncate(1 * _MB)
    for p in (big, small):
        (tmp_path / (p.name + ".bili_uploading")).touch()

    assert bili_uploader.release_small_placeholders(str(tmp_path)) == 1
    assert not (tmp_path / "a_001.mp4.bili_uploading").exists()
    assert (tmp_path / "a_000.mp4.bili_uploading").exists()


def test_session_date_label_uses_broadcast_date_not_today():
    """時間軸標題要用直播日期：場次可能在佇列裡排好幾天才投出去。"""
    d = "downloads/抖音直播/黃財神/2026-10-24_21-36"
    assert bili_uploader.session_date_label(d) == "2026-10-24 21:36"


def test_session_date_label_handles_date_only_dir():
    assert bili_uploader.session_date_label("x/2026-10-23") == "2026-10-23"


def test_session_date_label_falls_back_when_unparseable():
    out = bili_uploader.session_date_label("some/random/dir")
    assert len(out) == 10 and out[4] == "-"   # 退回今天 YYYY-MM-DD
