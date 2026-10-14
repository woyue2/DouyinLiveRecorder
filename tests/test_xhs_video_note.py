# -*- coding: utf-8 -*-
"""小紅書影片筆記 → MP3 → Markdown → 飛書 的鏈路測試。"""

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

DETAIL = {
    "publish_time": "2026-10-12 09:00:00",
    "image_list": ["https://img.example/cover.jpg"],
    "desc": "笔记正文",
}


def test_extract_video_url_new_ef_format_prefers_muxed_stream():
    """新版小紅書用 EF4/EF5… 作鍵；應挑含音軌的合流，而非純視頻流。"""
    card = {
        "video": {"media": {"stream": {
            "EF5": [{"master_url": "https://v/video-only.m4s"}],
            "EF4": [{"master_url": "https://v/muxed.mp4", "audio_codec": "mp4a"}],
            "EF6": [],
        }}}
    }
    assert xhs.extract_note_video_url(card) == "https://v/muxed.mp4"


def test_extract_video_url_legacy_codec_keys_still_work():
    card = {"video": {"media": {"stream": {
        "h264": [{"master_url": "https://v/h264.mp4", "audio_bitrate": 64}],
    }}}}
    assert xhs.extract_note_video_url(card) == "https://v/h264.mp4"


def test_extract_video_url_falls_back_when_no_muxed_stream():
    card = {"video": {"media": {"stream": {
        "EF5": [{"master_url": "https://v/only.m4s"}],
    }}}}
    assert xhs.extract_note_video_url(card) == "https://v/only.m4s"


def test_extract_video_url_handles_missing_or_empty():
    assert xhs.extract_note_video_url({}) == ""
    assert xhs.extract_note_video_url({"video": {}}) == ""
    assert xhs.extract_note_video_url({"video": {"media": {"stream": {}}}}) == ""
    assert xhs.extract_note_video_url({"video": {"media": {"stream": {"EF4": []}}}}) == ""


def test_doc_title_prefers_ai_title_when_transcript_exists(tmp_path):
    md = tmp_path / "t.md"
    md.write_text("# AI 提煉的標題\n\n## 章節\n正文\n", encoding="utf-8")
    title = xhs.build_note_doc_title("2026-10-14 12:00:00", "文主任", "原筆記標題",
                                     transcript_md_path=str(md))
    assert title == "2026-10-14【文主任】AI 提煉的標題"


def test_doc_title_falls_back_to_note_title_without_transcript():
    assert xhs.build_note_doc_title("2026-10-14 12:00:00", "文主任", "原筆記標題") == \
        "2026-10-14【文主任】原筆記標題"
    # 轉寫檔不存在時也應回退
    assert xhs.build_note_doc_title("2026-10-14 12:00:00", "文主任", "原筆記標題",
                                    transcript_md_path="/nope/missing.md") == \
        "2026-10-14【文主任】原筆記標題"


def test_feishu_markdown_uses_transcript_when_available(tmp_path):
    transcript = tmp_path / "t.md"
    transcript.write_text("# 原標題\n\n## 章節\n逐字稿內容\n", encoding="utf-8")

    md = xhs.build_note_feishu_markdown(
        DETAIL, "文主任", "測試標題", "42", transcript_md_path=str(transcript)
    )
    assert md.startswith("# 測試標題")
    assert "文主任" in md and "2026-10-12 09:00:00" in md and "42" in md
    assert "逐字稿內容" in md
    # 逐字稿自帶的 H1 應被移除，且不混入封面圖
    assert "原標題" not in md
    assert "cover.jpg" not in md


def test_feishu_markdown_falls_back_to_images_and_desc(tmp_path):
    missing = tmp_path / "nope.md"
    md = xhs.build_note_feishu_markdown(
        DETAIL, "文主任", "測試標題", "7", transcript_md_path=str(missing)
    )
    assert md.startswith("# 測試標題")
    assert "cover.jpg" in md
    assert "笔记正文" in md


def test_feishu_markdown_without_transcript_arg():
    md = xhs.build_note_feishu_markdown(DETAIL, "文主任", "標題", "0")
    assert "cover.jpg" in md and "笔记正文" in md


def test_process_video_note_returns_none_when_download_fails(tmp_path):
    class StubMonitor:
        def download_video(self, url, path):
            return False

    detail = dict(DETAIL, video_url="https://v.example/a.mp4")
    result = xhs.process_video_note(
        StubMonitor(), detail, "文主任", "標題", "nid1", str(tmp_path), "/bin/true"
    )
    assert result is None


def test_process_video_note_returns_none_without_transcribe_script(monkeypatch, tmp_path):
    """下載與抽音成功但未配置轉寫腳本時，不應崩潰，回傳 None。"""
    douyin = _load("douyin_video_monitor", ROOT / "douyin_video_monitor.py")

    class StubMonitor:
        def download_video(self, url, path):
            Path(path).write_bytes(b"fake")   # 模擬下載出檔案
            return True

    # process_video_note 內部才 import DouyinVideoMonitor，故直接改來源模組
    monkeypatch.setattr(douyin.DouyinVideoMonitor, "extract_mp3_from_mp4",
                        staticmethod(lambda mp4, mp3: True))
    monkeypatch.setattr(douyin.DouyinVideoMonitor, "transcribe_mp3_to_md",
                        staticmethod(lambda mp3, s: str(mp3) + ".md"))

    detail = dict(DETAIL, video_url="https://v.example/a.mp4")
    result = xhs.process_video_note(
        StubMonitor(), detail, "文主任", "標題", "nid2", str(tmp_path), ""
    )
    assert result is None
    # 影片本體在抽出音訊後應被刪除
    media_dir = tmp_path / "文主任_作品"
    assert not list(media_dir.glob("*.mp4"))
