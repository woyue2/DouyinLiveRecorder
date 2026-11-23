# -*- encoding: utf-8 -*-
"""內容閘門：判定一場直播是不是「帶貨直播」，是就整場不投、直接清掉。

為什麼要有：斌的世界下午場是純帶貨（原文就說「今天这一场直播呢是带货直播啊」），
傳到 B 站既沒人看，又要吃掉一天只有幾篇的投稿配額，還佔磁碟。

解耦原則跟 highlights 一樣：
- 只吃 (session_dir, video_paths, 設定, LLM)，不碰錄製/轉寫/投稿主流程；
- 判定失敗（沒有轉寫、LLM 掛了）一律回「不是帶貨」，照常上傳，絕不誤丟；
- 按主播名單啟用，預設關閉。

判定用兩段：先算帶貨詞密度（零成本），超過硬門檻直接判帶貨；
沒超過就問一次 LLM，模型說不出話時退回密度門檻。
"""
import glob
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:  # main.py 以 src.content_filter 匯入
    from . import llm_client
except ImportError:  # 直接以檔案載入（測試）
    import llm_client

try:
    from loguru import logger
except ImportError:
    import logging
    logger = logging.getLogger("content_filter")

# 帶貨直播的高頻詞；挑的是「只有在賣東西時才會出現」的，避免誤殺
_SELLING_KEYWORDS = (
    "带货", "小黄车", "购物车", "优惠券", "下单", "拍下", "库存",
    "秒杀", "福利价", "上车", "商品链接", "包邮", "抢购", "拍下了商品",
)

_JUDGE_SYSTEM = (
    "你是直播內容審核員。下面是一場抖音直播的轉寫片段。\n"
    "請判斷這場直播的主要內容是不是「直播帶貨」（主要在推銷、報價、引導下單買商品）。\n"
    "注意：知識分享、情感諮詢、聊天、才藝表演都不算帶貨，即使偶爾提到商品。\n"
    "只輸出 JSON：{\"selling\": true 或 false, \"reason\": \"20字以內的理由\"}"
)
_MAX_JUDGE_CHARS = 4000


@dataclass
class ContentFilterConfig:
    enabled: bool = False
    streamers: tuple = ()
    density_threshold: int = 15      # 每萬字命中數，超過就當帶貨
    hard_threshold: int = 25         # 超過這個不必問 LLM
    transcript_dir: str = "/home/ubuntu/1mp3tomd/output"
    api_key: str = ""
    api_base: str = ""
    model: str = ""
    delete_mp3: bool = True
    keep_md: bool = True

    def allows(self, record_name: str) -> bool:
        if not self.enabled or not self.streamers:
            return False
        name = record_name or ""
        return any(s and s in name for s in self.streamers)


def _get(cp, key, default=""):
    try:
        return cp.get("内容过滤", key, fallback=default)
    except Exception:
        return default


def _as_int(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_bool(value, default=False):
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in ("是", "1", "true", "yes", "on")


def load_config(cp) -> ContentFilterConfig:
    if cp is None or not getattr(cp, "has_section", lambda _s: False)("内容过滤"):
        return ContentFilterConfig()
    raw = _get(cp, "启用主播", "")
    streamers = tuple(s.strip() for s in re.split(r"[,，\s]+", raw or "") if s.strip())
    return ContentFilterConfig(
        enabled=_as_bool(_get(cp, "启用", "否"), False),
        streamers=streamers,
        density_threshold=max(1, _as_int(_get(cp, "关键词密度阈值", 15), 15)),
        hard_threshold=max(1, _as_int(_get(cp, "硬阈值", 25), 25)),
        transcript_dir=str(_get(cp, "转写目录", "/home/ubuntu/1mp3tomd/output")).strip(),
        api_key=str(_get(cp, "api_key", "")).strip(),
        api_base=str(_get(cp, "api_base", "")).strip(),
        model=str(_get(cp, "模型", "")).strip(),
        delete_mp3=_as_bool(_get(cp, "删除mp3", "是"), True),
        keep_md=_as_bool(_get(cp, "保留md", "是"), True),
    )


def keyword_density(text: str, keywords=_SELLING_KEYWORDS) -> float:
    """每萬字命中次數；空文本回 0。"""
    if not text:
        return 0.0
    return sum(text.count(k) for k in keywords) / max(len(text), 1) * 10000


def _read_session_text(session_dir: str, video_paths: list, cfg: ContentFilterConfig) -> str:
    """找這場的文字：segments.json → 轉寫 txt → md。

    順序是按「上傳時還在不在」排的：md 常被百度雲上傳後刪掉，segments.json
    我們自己留著，轉寫目錄的 txt 也不會被清，所以它最可靠。
    """
    chunks = []
    for raw in video_paths or []:
        stem = Path(raw).stem
        seg = Path(raw).with_suffix(".segments.json")
        if seg.exists():
            try:
                data = json.loads(seg.read_text(encoding="utf-8"))
                chunks.append("".join(str(s.get("text") or "") for s in data.get("segments") or []))
                continue
            except (OSError, json.JSONDecodeError):
                pass
        if cfg.transcript_dir:
            txt = Path(cfg.transcript_dir) / f"{stem}.txt"
            if txt.exists():
                try:
                    chunks.append(txt.read_text(encoding="utf-8", errors="ignore"))
                    continue
                except OSError:
                    pass
        md = Path(raw).with_suffix(".md")
        if md.exists():
            try:
                chunks.append(md.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                pass
    return "".join(chunks)


def judge(session_dir: str, video_paths: list, record_name: str,
          cfg: ContentFilterConfig, api_key: Optional[str] = None,
          api_base: Optional[str] = None, model: str = "deepseek-chat"
          ) -> tuple:
    """回 (是否帶貨, 原因)。任何不確定都回 False（照常上傳）。"""
    if not cfg.allows(record_name):
        return False, ""
    try:
        text = _read_session_text(session_dir, video_paths, cfg)
        if len(text) < 200:
            logger.info(f"內容過濾：{record_name} 沒有可用轉寫，跳過判定")
            return False, ""

        density = keyword_density(text)
        if density >= cfg.hard_threshold:
            logger.info(f"內容過濾：{record_name} 帶貨詞密度 {density:.1f}/萬字，判定為帶貨")
            return True, f"密度 {density:.1f}"

        api_key = cfg.api_key or api_key
        api_base = cfg.api_base or api_base
        model = cfg.model or model
        if api_key:
            reply = llm_client.chat(_JUDGE_SYSTEM, text[:_MAX_JUDGE_CHARS],
                                    api_key=api_key, api_base=api_base,
                                    model=model, max_tokens=200, temperature=0)
            data = llm_client.extract_json(reply or "")
            if isinstance(data, dict) and isinstance(data.get("selling"), bool):
                reason = str(data.get("reason") or "")
                logger.info(f"內容過濾：{record_name} LLM 判定帶貨={data['selling']}（{reason}）")
                return data["selling"], reason

        verdict = density >= cfg.density_threshold
        logger.info(f"內容過濾：{record_name} LLM 不可用，按密度 {density:.1f}/萬字 判定帶貨={verdict}")
        return verdict, f"密度 {density:.1f}"
    except Exception as e:
        logger.warning(f"內容過濾異常，照常上傳: {type(e).__name__}: {e}")
        return False, ""


def discard_media(session_dir: str, video_paths: list,
                  cfg: ContentFilterConfig) -> int:
    """刪掉這場的影片與 mp3（帶貨場不留），md 依設定保留。回傳刪除檔數。"""
    removed = 0
    targets = []
    for raw in video_paths or []:
        targets.extend([raw, str(Path(raw).with_suffix(".mp3")),
                        raw + ".bili_uploading"])
    targets.extend(glob.glob(os.path.join(session_dir, "*.part")))
    for path in targets:
        try:
            if os.path.exists(path):
                os.remove(path)
                removed += 1
        except OSError as e:
            logger.warning(f"刪除帶貨場檔案失敗 {path}: {e}")
    if not cfg.keep_md:
        for md in glob.glob(os.path.join(session_dir, "*.md")):
            try:
                os.remove(md)
                removed += 1
            except OSError:
                pass
    return removed
