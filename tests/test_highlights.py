# -*- coding: utf-8 -*-
"""精彩片段筛选的純邏輯測試。

模型輸出是不可信的：段號可能不存在、區間可能超出片長、可能太短、可能重疊。
這些修正邏輯都在 normalize_ranges / load_config 裡，必須有測試釘住，
否則一旦失控就是「剪出空片段」或「整場被丟掉」。
"""

import configparser
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

_spec = importlib.util.spec_from_file_location("highlights", SRC / "highlights.py")
hl = importlib.util.module_from_spec(_spec)
sys.modules["highlights"] = hl
_spec.loader.exec_module(hl)


def _cp(section: dict) -> configparser.ConfigParser:
    cp = configparser.ConfigParser()
    cp.add_section("精彩片段")
    for k, v in section.items():
        cp.set("精彩片段", k, v)
    return cp


def test_missing_section_means_disabled():
    cfg = hl.load_config(configparser.ConfigParser())
    assert cfg.enabled is False
    assert cfg.allows("序号6 初恋小娜") is False


def test_load_config_reads_streamers_and_limits():
    cfg = hl.load_config(_cp({
        "启用": "是",
        "启用主播": "初恋小娜，犬傲天",
        "模式": "part",
        "最短片段(秒)": "30",
        "最长片段(秒)": "180",
        "目标时长(分钟)": "25",
        "最多保留段数": "4",
        "每段最多片段数": "3",
        "播出后删除原片": "否",
    }))
    assert cfg.enabled is True
    assert cfg.streamers == ("初恋小娜", "犬傲天")
    assert cfg.mode == "part"
    assert (cfg.min_clip_seconds, cfg.max_clip_seconds) == (30, 180)
    assert cfg.target_minutes == 25
    assert cfg.max_parts == 4
    assert cfg.max_clips_per_part == 3
    assert cfg.delete_originals is False


def test_allows_only_listed_streamers():
    cfg = hl.load_config(_cp({"启用": "是", "启用主播": "初恋小娜"}))
    assert cfg.allows("序号6 初恋小娜") is True
    assert cfg.allows("序号11 犬傲天") is False


def test_normalize_clamps_out_of_range():
    clips = hl.normalize_ranges(
        [{"start": -30, "end": 9999, "title": "x"}],
        duration=600, min_seconds=45, max_seconds=240, limit=3)
    assert len(clips) == 1
    assert clips[0]["start"] == 0
    assert clips[0]["end"] == 240, "超過最長時長要截斷，且從 0 起算"


def test_normalize_extends_too_short_clip():
    """太短的區間往後補到最短時長，而不是直接丟掉。"""
    clips = hl.normalize_ranges(
        [{"start": 100, "end": 105, "title": "短"}],
        duration=600, min_seconds=45, max_seconds=240, limit=3)
    assert len(clips) == 1
    assert clips[0]["end"] - clips[0]["start"] == 45


def test_normalize_merges_overlaps_and_sorts():
    clips = hl.normalize_ranges(
        [{"start": 300, "end": 400}, {"start": 50, "end": 120}, {"start": 90, "end": 200}],
        duration=600, min_seconds=45, max_seconds=240, limit=5)
    assert [(c["start"], c["end"]) for c in clips] == [(50, 200), (300, 400)]


def test_normalize_drops_garbage_and_applies_limit():
    clips = hl.normalize_ranges(
        [{"start": "abc", "end": 10}, {}, {"start": 500, "end": 560},
         {"start": 100, "end": 160}, {"start": 200, "end": 260}],
        duration=600, min_seconds=45, max_seconds=240, limit=2)
    assert len(clips) == 2
    assert clips[0]["start"] == 100


def test_build_meta_matches_upload_shape():
    meta = hl.build_meta(
        {"title": "露娜五杀合集", "desc": ""},
        clips=["/a/1.mp4", "/a/2.mp4"],
        clip_titles=["极限反杀", "团战收割"],
        part_labels=[1, 1],
        record_name="序号11 王者荣耀有痕",
    )
    assert meta["title"] == "露娜五杀合集"
    assert len(meta["parts"]) == 2
    assert meta["parts"][0]["label"] == "【P1】"
    assert "极限反杀" in meta["parts"][0]["title"]
    assert meta["part_descs"] == ["极限反杀", "团战收割"]
    assert meta["tags"].startswith("王者荣耀有痕")


def test_collect_parts_uses_segments_duration(tmp_path):
    """有 .segments.json 時不必再 ffprobe，時長直接從時間軸拿。"""
    video = tmp_path / "part000.mp4"
    video.write_bytes(b"x" * 10)
    (tmp_path / "part000.segments.json").write_text(
        '{"duration": 900.0, "segments": [{"start": 0, "end": 5, "text": "嗨"}]}',
        encoding="utf-8")
    (tmp_path / "part000.md").write_text(
        "# 露娜教学\n\n<!--SUMMARY_START-->\n> 💡 **精彩看点**\n> 1. 反杀很精彩\n<!--SUMMARY_END-->\n",
        encoding="utf-8")

    parts = hl.collect_parts([str(video)])
    assert len(parts) == 1
    assert parts[0]["duration"] == 900.0
    assert parts[0]["title"] == "露娜教学"
    assert "反杀很精彩" in parts[0]["bullets"][0]


def test_collect_parts_skips_too_short(tmp_path):
    video = tmp_path / "part000.mp4"
    video.write_bytes(b"x" * 10)
    (tmp_path / "part000.segments.json").write_text(
        '{"duration": 12.0, "segments": [{"start": 0, "end": 5, "text": "嗨"}]}',
        encoding="utf-8")
    assert hl.collect_parts([str(video)]) == []
