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

