# -*- encoding: utf-8 -*-
"""「一句」精簡摘要群。

內容群發完整通知（看點、連結、狀態），這裡只留一行：
    MM-DD HH:MM ｜抖音｜泰勒｜男人三四十岁的顶级状态：身材、钱、心态

用途是一眼掃完「今天產出了什麼、哪個平台、誰的」。不帶連結是刻意的：
混進 URL 會讓每條長三倍，失去掃讀價值；要點進去再回內容群找同名那條。

設定鍵：config/local_secrets.ini [推送配置] 钉钉一句接口链接
留空則完全不發，不影響既有流程。
"""
import configparser
import datetime
import os
import sys

_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)

import msg_push

_MAX_SENTENCE = 60
_SENTENCE_END = "。！？!?\n"


def _load_webhook() -> str:
    for name in ("local_secrets.ini", "config.ini"):
        path = os.path.join(_CURRENT_DIR, "config", name)
        if not os.path.exists(path):
            continue
        try:
            cp = configparser.RawConfigParser()
            cp.read(path, encoding="utf-8-sig")
            got = cp.get("推送配置", "钉钉一句接口链接", fallback="").strip()
            if got:
                return got
        except Exception:
            continue
    return ""


def to_one_sentence(text: str) -> str:
    """取第一句並截斷，用來把長摘要壓成一行。"""
    text = (text or "").strip().replace("\r", " ")
    for i, ch in enumerate(text):
        if ch in _SENTENCE_END:
            text = text[:i]
            break
    text = " ".join(text.split())
    if len(text) > _MAX_SENTENCE:
        text = text[:_MAX_SENTENCE].rstrip() + "…"
    return text


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
        return bool(res.get("success"))
    except Exception:
        return False
