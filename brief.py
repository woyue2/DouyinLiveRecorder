# -*- encoding: utf-8 -*-
"""「一句」精簡摘要群。

內容群發完整通知（看點、連結、狀態），這裡只留一行：
    MM-DD HH:MM ｜抖音｜泰勒｜男人三四十岁的顶级状态：身材、钱、心态

用途是一眼掃完「今天產出了什麼、哪個平台、誰的」。不帶連結是刻意的：
混進 URL 會讓每條長三倍，失去掃讀價值；要點進去再回內容群找同名那條。

設定鍵：config/local_secrets.ini [推送配置] 钉钉一句接口链接
留空則完全不發，不影響既有流程。
"""
import datetime
import logging
import os
import re
import sys

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

import msg_push
import notify_route

logger = logging.getLogger("brief")

_webhook_cache = None  # (mtime 元組, webhook)，見 _load_webhook

_MAX_SENTENCE = 60
_SENTENCE_END = "。！？!?\n"
# 摘要區塊的第一行通常只是標題（「💡 **精彩看点**」），不該當成內容
_LABEL_ONLY = {"精彩看点", "精彩看點", "看点", "看點", "摘要", "总结", "總結",
               "核心内容", "核心內容", "重点", "重點"}


def _clean_line(line: str) -> str:
    """去掉 markdown 標記、行首 emoji 與序號，留下可讀文字。"""
    line = line.replace("**", "").replace("__", "").lstrip("#> ").strip()
    # 序號必須以空白或行尾收尾才算序號，否則「3.15 消費者權益日」會被砍成
    # 「15 消費者權益日」。中文常見的「1、」「1）」沒有這個歧義，照砍。
    line = re.sub(r"^\d+\.\s+", "", line)
    line = re.sub(r"^\d+[、)）]\s*", "", line)
    line = re.sub(r"^[①-⑳]\s*", "", line)
    # 行首的裝飾字元（emoji、項目符號）不對應任何文字或數字
    line = re.sub(r"^[^\w一-鿿]+", "", line)
    # 標題行可能寫成「精彩看点：」，結尾標點一併去掉才吃得進 _LABEL_ONLY
    return line.strip().strip("：:　 ")


def _load_webhook() -> str:
    """讀取「一句」群 Webhook，依檔案 mtime 快取。

    合併讀取交給 notify_route，避免出現第三套「哪些檔、什麼順序」的實作。
    一次監控會發十幾條，設定檔沒變就不重讀（local_secrets 內含數千字 cookie）。
    """
    global _webhook_cache
    paths = [os.path.join(_CURRENT_DIR, "config", name)
             for name in ("local_secrets.ini", "config.ini")]
    try:
        stamp = tuple(os.path.getmtime(p) if os.path.exists(p) else 0 for p in paths)
    except OSError:
        stamp = None
    if _webhook_cache and stamp is not None and stamp == _webhook_cache[0]:
        return _webhook_cache[1]

    cp = notify_route.merge_config_files(*paths)
    value = cp.get("推送配置", "钉钉一句接口链接", fallback="").strip()         if cp.has_section("推送配置") else ""
    if stamp is not None:
        _webhook_cache = (stamp, value)
    return value


def to_one_sentence(text: str) -> str:
    """取第一句並截斷，用來把長摘要壓成一行。

    摘要常以「💡 **精彩看点**」這種標題行開頭，直接取第一個換行前的內容會
    得到標題而不是重點，所以先逐行清洗、跳過標題行與過短殘片。
    """
    for raw_line in (text or "").splitlines():
        line = _clean_line(raw_line)
        if not line or line in _LABEL_ONLY or len(line) < 2:
            continue
        for i, ch in enumerate(line):
            if ch in _SENTENCE_END:
                line = line[:i]
                break
        line = " ".join(line.split())
        if len(line) < 2:
            continue
        if len(line) > _MAX_SENTENCE:
            line = line[:_MAX_SENTENCE].rstrip() + "…"
        return line
    return ""


def format_duration(seconds: float) -> str:
    """把秒數寫成「2 小时 13 分」／「45 分钟」這種口語時長。"""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    if hours:
        return f"{hours} 小时 {remainder // 60} 分"
    return f"{max(1, remainder // 60)} 分钟"


def push(kind: str, name: str, sentence: str) -> bool:
    """發一條濃縮摘要。任何失敗都只記錄、不影響主流程。"""
    webhook = _load_webhook()
    if not webhook:
        return False
    sentence = to_one_sentence(sentence)
    if not sentence:
        return False
    stamp = datetime.datetime.now().strftime("%m-%d %H:%M")
    # 類別用【】包起來：釘釘機器人的「自定義關鍵詞」多半設成【，
    # 這樣既能過安檢，掃讀時也比純文字標籤更醒目。
    line = f"{stamp} ｜【{kind}】｜{name}｜{sentence}"
    try:
        res = msg_push.dingtalk(webhook, line, "", False)
    except Exception as e:
        logger.warning(f"一句摘要發送異常: {e}")
        return False
    if res.get("success"):
        return True
    # 失敗不能靜默：關鍵詞不匹配這類問題會讓整個群一條都收不到
    logger.warning(f"一句摘要發送失敗: {res.get('error')}")
    return False
