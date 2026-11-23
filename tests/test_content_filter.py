# -*- coding: utf-8 -*-
"""內容閘門測試：帶貨直播不投、正常直播照常投。

重點守住兩件事：
1. 判定不確定時必須回「不是帶貨」——寧可多傳一場，也不能誤丟內容；
2. 只有名單內的主播會被判定，其他人完全不經過這條路。
"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

_spec = importlib.util.spec_from_file_location("content_filter", SRC / "content_filter.py")
cf = importlib.util.module_from_spec(_spec)
sys.modules["content_filter"] = cf
_spec.loader.exec_module(cf)


def _cp(section: dict):
    import configparser
    cp = configparser.ConfigParser()
    cp.add_section("内容过滤")
    for k, v in section.items():
        cp.set("内容过滤", k, v)
    return cp


def test_missing_section_disables():
    import configparser
    cfg = cf.load_config(configparser.ConfigParser())
    assert cfg.enabled is False
    assert cfg.allows("序号1 斌的世界") is False


def test_streamer_list_and_limits():
    cfg = cf.load_config(_cp({
        "启用": "是",
        "启用主播": "斌的世界",
        "关键词密度阈值": "12",
        "删除mp3": "是",
    }))
    assert cfg.allows("序号1 斌的世界") is True
    assert cfg.allows("序号2 斌的解析") is False
    assert cfg.density_threshold == 12


def test_density_counts_per_ten_thousand():
    assert cf.keyword_density("") == 0.0
    text = "带货" * 10          # 20 字，命中 10 次
    assert cf.keyword_density(text) == 10 / 20 * 10000


def test_high_density_short_circuits_without_llm(tmp_path, monkeypatch):
    """密度超硬門檻直接判帶貨，不打 LLM（省錢也省時間）。"""
    video = tmp_path / "p.mp4"
    video.write_bytes(b"x")
    (tmp_path / "p.md").write_text("带货" * 100, encoding="utf-8")

    cfg = cf.load_config(_cp({"启用": "是", "启用主播": "斌的世界",
                              "硬阈值": "20"}))
    called = {"n": 0}
    monkeypatch.setattr(cf.llm_client, "chat",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or "{}")
    selling, reason = cf.judge(str(tmp_path), [str(video)], "序号1 斌的世界",
                               cfg, api_key="k", api_base=None, model="m")
    assert selling is True
    assert called["n"] == 0


def test_llm_verdict_is_used(tmp_path, monkeypatch):
    video = tmp_path / "p.mp4"
    video.write_bytes(b"x")
    (tmp_path / "p.md").write_text("今天聊情感，顺便提一下商品链接。" * 20, encoding="utf-8")
    cfg = cf.load_config(_cp({"启用": "是", "启用主播": "斌的世界",
                              "硬阈值": "999", "关键词密度阈值": "999"}))
    monkeypatch.setattr(cf.llm_client, "chat",
                        lambda *a, **k: '{"selling": true, "reason": "全程推销"}')
    selling, reason = cf.judge(str(tmp_path), [str(video)], "序号1 斌的世界",
                               cfg, api_key="k", api_base=None, model="m")
    assert selling is True and "推销" in reason


def test_llm_failure_falls_back_to_not_selling(tmp_path, monkeypatch):
    """LLM 掛掉且密度沒超標 → 必須照常上傳，不能誤丟。"""
    video = tmp_path / "p.mp4"
    video.write_bytes(b"x")
    (tmp_path / "p.md").write_text("今天聊聊感情问题。" * 20, encoding="utf-8")
    cfg = cf.load_config(_cp({"启用": "是", "启用主播": "斌的世界",
                              "硬阈值": "999", "关键词密度阈值": "15"}))
    monkeypatch.setattr(cf.llm_client, "chat", lambda *a, **k: None)
    selling, _ = cf.judge(str(tmp_path), [str(video)], "序号1 斌的世界",
                          cfg, api_key="k", api_base=None, model="m")
    assert selling is False


def test_no_transcript_means_not_selling(tmp_path):
    video = tmp_path / "p.mp4"
    video.write_bytes(b"x")
    cfg = cf.load_config(_cp({"启用": "是", "启用主播": "斌的世界",
                              "转写目录": str(tmp_path / "none")}))
    selling, _ = cf.judge(str(tmp_path), [str(video)], "序号1 斌的世界",
                          cfg, api_key="k", api_base=None, model="m")
    assert selling is False


def test_streamer_outside_list_never_judged(tmp_path, monkeypatch):
    video = tmp_path / "p.mp4"
    video.write_bytes(b"x")
    (tmp_path / "p.md").write_text("带货" * 100, encoding="utf-8")
    cfg = cf.load_config(_cp({"启用": "是", "启用主播": "斌的世界"}))
    called = {"n": 0}
    monkeypatch.setattr(cf.llm_client, "chat",
                        lambda *a, **k: called.__setitem__("n", 1))
    selling, _ = cf.judge(str(tmp_path), [str(video)], "序号2 斌的解析",
                          cfg, api_key="k")
    assert selling is False and called["n"] == 0


def test_discard_removes_video_and_mp3_keeps_md(tmp_path):
    video = tmp_path / "p.mp4"
    video.write_bytes(b"x")
    (tmp_path / "p.mp3").write_bytes(b"x")
    (tmp_path / "p.md").write_text("x", encoding="utf-8")
    cfg = cf.load_config(_cp({"启用": "是", "启用主播": "斌的世界"}))

    removed = cf.discard_media(str(tmp_path), [str(video)], cfg)
    assert removed == 2
    assert not video.exists() and not (tmp_path / "p.mp3").exists()
    assert (tmp_path / "p.md").exists(), "md 依設定保留"
