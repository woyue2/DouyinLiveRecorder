# -*- coding: utf-8 -*-
"""回歸測試：小紅書熔斷偵測與抖音例外處理。

對應 2026-10-14 全面審查發現的缺陷：
- 小紅書：code=-100（登入態失效）與「200 但回非 JSON（HTML 驗證頁）」都被靜默放行，
  不會觸發 24 小時熔斷，帳號失效被當成「本次無新內容」。
- 抖音：extract_mp3_from_mp4 只捕捉 CalledProcessError，系統無 ffmpeg 時
  FileNotFoundError 會穿透，導致整批中止且不寫 history。
"""

import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

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
douyin = _load("douyin_video_monitor", ROOT / "douyin_video_monitor.py")


def _monitor():
    return xhs.XhsNoteMonitor(cookie="")


# ---------- 小紅書熔斷偵測 ----------

def test_risk_control_raises_on_http_461():
    resp = httpx.Response(461, headers={"Verifytype": "301", "Verifyuuid": "abc"})
    with pytest.raises(xhs.XhsRiskControlError):
        _monitor()._check_risk_control(resp, "/api/sns/web/v1/user_posted")


def test_risk_control_raises_on_login_expired_code_minus_100():
    """code=-100 代表登入態失效，必須熔斷告警而非靜默忽略。"""
    resp = httpx.Response(200, json={"code": -100, "success": False, "msg": "登录已过期"})
    with pytest.raises(xhs.XhsRiskControlError, match="-100"):
        _monitor()._check_risk_control(resp, "/api/sns/web/v1/user_posted")


def test_risk_control_raises_on_non_json_200():
    """200 卻回 HTML（驗證頁／攔截頁）必須視為風控，不可靜默放行。"""
    resp = httpx.Response(200, text="<html><body>请完成安全验证</body></html>",
                          headers={"Content-Type": "text/html"})
    with pytest.raises(xhs.XhsRiskControlError, match="非 JSON"):
        _monitor()._check_risk_control(resp, "/api/sns/web/v1/user_posted")


def test_risk_control_raises_on_rate_limit_codes():
    for code in (300012, 300015):
        resp = httpx.Response(200, json={"code": code, "success": False, "msg": "访问频繁"})
        with pytest.raises(xhs.XhsRiskControlError):
            _monitor()._check_risk_control(resp, "/api/sns/web/v1/user_posted")


def test_risk_control_allows_normal_success():
    resp = httpx.Response(200, json={"code": 0, "success": True, "msg": "成功", "data": {"notes": []}})
    _monitor()._check_risk_control(resp, "/api/sns/web/v1/user_posted")  # 不應拋錯


def test_risk_control_raises_on_403_429():
    for status in (403, 429):
        with pytest.raises(xhs.XhsRiskControlError):
            _monitor()._check_risk_control(httpx.Response(status), "/x")


# ---------- 抖音例外處理 ----------

def test_extract_mp3_returns_false_when_ffmpeg_missing(monkeypatch, tmp_path):
    """系統沒有 ffmpeg 時不可讓例外穿透（否則整批中止、不寫 history）。"""
    mp4 = tmp_path / "a.mp4"
    mp4.write_bytes(b"fake")

    def _raise(*a, **kw):
        raise FileNotFoundError("ffmpeg not found")

    monkeypatch.setattr(douyin.subprocess, "run", _raise)
    assert douyin.DouyinVideoMonitor.extract_mp3_from_mp4(str(mp4), str(tmp_path / "a.mp3")) is False


def test_extract_mp3_returns_false_on_unexpected_error(monkeypatch, tmp_path):
    mp4 = tmp_path / "a.mp4"
    mp4.write_bytes(b"fake")

    def _raise(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr(douyin.subprocess, "run", _raise)
    assert douyin.DouyinVideoMonitor.extract_mp3_from_mp4(str(mp4), str(tmp_path / "a.mp3")) is False


def test_douyin_throttle_constants_meet_doctrine():
    """抖音也必須遵守「博主間隔 >= 10 分鐘、作品間隔 >= 3 分鐘」的風控紀律。"""
    assert douyin.BLOGGER_GAP_RANGE[0] >= 600
    assert douyin.VIDEO_GAP_RANGE[0] >= 180
