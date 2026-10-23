# -*- encoding: utf-8 -*-
"""
小紅書博主作品（圖文/視頻）監控與精編流水線
【高強度風控防護專用版】
1. 嚴格熔斷機制：一旦遇到 HTTP 461/471 驗證碼或風控阻斷，立即休眠 24 小時並推送釘釘告警
2. 隨機抖動延遲：博主間間隔 5~12 秒，筆記間間隔 2.5~6 秒，模擬真實用戶行為
3. 帳號隔離保護：支援配置獨立小號 Cookie，避免主力個人帳號受波及
4. 自動識別圖文 (normal) 與視頻 (video)，自動提煉看點並生成高顏值手機閱讀網頁
"""

import os
import re
import sys
import time
import json
import random
import logging
import argparse
import datetime
import subprocess
import configparser
import urllib.parse
from typing import List, Dict, Optional, Any, Tuple

import httpx
try:
    from xhshow import Xhshow
except ImportError:
    Xhshow = None

# 確保可以載入 src 下的 md_to_html 與 msg_push
_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)
_SRC_DIR = os.path.join(_CURRENT_DIR, "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

import msg_push
from md_to_html import HTML_TEMPLATE, render_markdown_inline
from filename_utils import sanitize_filename_part

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("XhsNoteMonitor")

# note_id 前 8 位十六進位為建立時間戳；早於此時間視為解析失敗（不會是小紅書真實貼文）
_NOTE_ID_TS_MIN = 1577808000.0   # 2020-01-01 00:00:00 UTC+8

# 影片下載連續失敗幾次後放棄（避免一封壞掉的筆記每天重下載整個影片）
_XHS_MAX_DOWNLOAD_ATTEMPTS = 3


class XhsRiskControlError(Exception):
    """小紅書風控/人機驗證碼阻斷異常"""
    pass


class CooldownManager:
    """
    風控熔斷管理器
    記錄冷卻狀態至本地檔案，保護 IP 與小紅書帳號
    """
    def __init__(self, filepath: str = "./downloads/xhs_cooldown.json"):
        self.filepath = filepath

    def check_cooldown(self) -> Tuple[bool, float, str]:
        """
        檢查目前是否處於風控冷卻期
        返回 (是否處於冷卻期, 剩餘小時數, 觸發原因)
        """
        if not os.path.exists(self.filepath):
            return False, 0.0, ""

        try:
            with open(self.filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            expire_at = data.get("expire_at", 0)
            reason = data.get("reason", "未知風控原因")
            now = time.time()
            if now < expire_at:
                remaining_hours = (expire_at - now) / 3600.0
                return True, remaining_hours, reason
        except Exception as e:
            logger.warning(f"讀取冷卻紀錄檔案失敗: {e}")

        return False, 0.0, ""

    def activate_cooldown(
        self,
        hours: float = 24.0,
        reason: str = "",
        dingtalk_webhook: str = "",
        dingtalk_phone: str = "",
        dingtalk_is_atall: bool = False,
        feishu_webhook: str = "",
        login_url: str = ""
    ):
        """激活風控熔斷保護：寫入冷卻記錄並發送釘釘緊急報警"""
        now = time.time()
        expire_at = now + (hours * 3600.0)
        expire_dt = datetime.datetime.fromtimestamp(expire_at).strftime("%Y-%m-%d %H:%M:%S")

        os.makedirs(os.path.dirname(os.path.abspath(self.filepath)), exist_ok=True)
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump({
                    "triggered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "expire_at": expire_at,
                    "expire_dt": expire_dt,
                    "cooldown_hours": hours,
                    "reason": reason
                }, f, ensure_ascii=False, indent=2)
            logger.error(f"⚠️ [風控熔斷激活] 系統將自動休眠 {hours:.1f} 小時（至 {expire_dt}），原因: {reason}")
        except Exception as e:
            logger.error(f"寫入冷卻紀錄檔案失敗: {e}")

        # 發送釘釘緊急警報
        if dingtalk_webhook:
            alert_msg = (
                f"🚨 【小红书风控紧急告警】\n\n"
                f"原因：{reason}\n"
                f"状态：已触发系统熔断保护机制！\n"
                f"策略：自动暂停所有小红书抓取请求 {hours:.1f} 小时，避免封号/封IP。\n"
                f"恢复时间预计为：{expire_dt}\n\n"
                f"提示：请勿手动频繁重试，静置 24 小时后系统将自动恢复巡检。"
            )
            try:
                msg_push.dingtalk(dingtalk_webhook, alert_msg, dingtalk_phone, dingtalk_is_atall)
                logger.info("釘釘風控緊急警報發送成功")
            except Exception as e:
                logger.error(f"發送釘釘風控警報失敗: {e}")

        # 登入態失效要人工掃碼，單獨再發一則帶「真連結」的通知：
        # 點開就是伺服器上的登入頁，掃完 cookie 自動寫回，不必手動搬檔。
        if self._is_login_expired(reason):
            self._notify_login_needed(
                feishu_webhook=feishu_webhook,
                dingtalk_webhook=dingtalk_webhook,
                dingtalk_phone=dingtalk_phone,
                dingtalk_is_atall=dingtalk_is_atall,
                login_url=login_url,
                expire_dt=expire_dt,
                reason=reason,
            )

    @staticmethod
    def _is_login_expired(reason: str) -> bool:
        text = reason or ""
        return ("登录已过期" in text or "登入態失效" in text or "請更新 Cookie" in text
                or "请更新 Cookie" in text)

    def _notify_login_needed(self, feishu_webhook: str, dingtalk_webhook: str,
                             dingtalk_phone: str, dingtalk_is_atall: bool,
                             login_url: str, expire_dt: str, reason: str) -> None:
        link_line = (f"\n點此掃碼登入（伺服器端，掃完自動寫回 cookie）：\n{login_url}\n"
                     f"\n二維碼約 3 分鐘失效，過期就重新打開同一個連結。\n"
                     if login_url else
                     "\n（未設定登入連結：請確認 xhs_login_web 服務與 token 檔）\n")
        msg = (
            f"🔑 【小紅書需要重新登入】\n\n"
            f"原因：{reason}\n"
            f"已暫停抓取，預計恢復：{expire_dt}\n"
            + link_line +
            f"\n登入完成後熔斷記錄會自動清除，下一次巡檢就會用新的 cookie。"
        )
        try:
            if feishu_webhook:
                msg_push.feishu_bot(feishu_webhook, msg)
                logger.info("飛書登入提醒發送成功")
            elif dingtalk_webhook:
                msg_push.dingtalk(dingtalk_webhook, msg, dingtalk_phone, dingtalk_is_atall)
                logger.info("登入提醒已改發到釘釘（未設定飛書 webhook）")
            else:
                logger.warning("登入提醒未發送：沒有飛書或釘釘 webhook")
        except Exception as e:
            logger.error(f"發送登入提醒失敗: {e}")


def xhs_login_url() -> str:
    """伺服器掃碼登入頁的真連結（含 token）。

    token 由 tools/xhs_login_web.py 首次啟動時產生並寫入
    config/xhs_login_token.txt；取不到就回空字串，通知會提示未設定。
    """
    base = os.environ.get("XHS_LOGIN_BASE_URL", "http://101.35.47.249:8080").rstrip("/")
    token_path = os.path.join(_CURRENT_DIR, "config", "xhs_login_token.txt")
    try:
        with open(token_path, "r", encoding="utf-8") as f:
            token = f.read().strip()
    except OSError:
        return ""
    return f"{base}/xhs-login?t={token}" if token else ""


def load_project_config() -> Dict[str, Any]:
    """讀取專案 config.ini 中的小紅書配置、轉寫與推送設定"""
    config = configparser.RawConfigParser()
    possible_paths = [
        os.path.join(_CURRENT_DIR, "config", "config.ini"),
        os.path.join(_CURRENT_DIR, "config.ini"),
    ]
    for p in possible_paths:
        if os.path.exists(p):
            config.read(p, encoding="utf-8-sig")
            break

    # 若存在本地私密設定檔 (例如 local_secrets.ini)，一併讀入覆蓋
    for sp in [os.path.join(_CURRENT_DIR, "config", "local_secrets.ini"), os.path.join(_CURRENT_DIR, "local_secrets.ini")]:
        if os.path.exists(sp):
            config.read(sp, encoding="utf-8-sig")
            break

    settings = {
        "xhs_cookie": "",
        "transcribe_script": "",
        "dingtalk_webhook": "",
        "dingtalk_phone": "",
        "dingtalk_is_atall": False,
        "dingtalk_enabled": False,
        "feishu_webhook": "",
        "web_enabled": True,
        "web_outdir": "./web_posts",
        "web_base_url": "http://101.35.47.249:8080/posts",
        "web_retention_days": 30,
        "llm_api_key": "",
        "llm_api_base": "https://api.minimaxi.com/anthropic",
        "llm_model": "MiniMax-M3",
        "feishu_enabled": False,
        "feishu_app_id": "",
        "feishu_app_secret": "",
        "feishu_folder_token": "",
    }

    if config.has_section("Cookie"):
        settings["xhs_cookie"] = config.get("Cookie", "小红书cookie", fallback="").strip()

    if config.has_section("录制设置"):
        settings["transcribe_script"] = config.get("录制设置", "mp3转写md脚本路径", fallback="").strip()

    if config.has_section("推送配置"):
        push_types = config.get("推送配置", "直播状态推送渠道", fallback="").strip()
        settings["dingtalk_webhook"] = config.get("推送配置", "钉钉推送接口链接", fallback="").strip()
        settings["dingtalk_phone"] = config.get("推送配置", "钉钉通知@对象(填手机号)", fallback="").strip()
        at_all_str = config.get("推送配置", "钉钉通知@全体(是/否)", fallback="否").strip()
        settings["dingtalk_is_atall"] = at_all_str in {"是", "true", "1", "yes"}
        settings["dingtalk_enabled"] = bool("钉钉" in push_types and settings["dingtalk_webhook"])
        settings["feishu_webhook"] = config.get("推送配置", "飞书webhook", fallback="").strip()

    if config.has_section("Web服务配置"):
        settings["web_enabled"] = config.get("Web服务配置", "启用网页生成", fallback="是").strip() in {"是", "true", "1", "yes"}
        settings["web_outdir"] = config.get("Web服务配置", "网页输出目录", fallback="./web_posts").strip()
        settings["web_base_url"] = config.get("Web服务配置", "网页访问地址", fallback="http://101.35.47.249:8080/posts").strip()
        settings["web_retention_days"] = config.getint("Web服务配置", "网页保留天数", fallback=30)

    if config.has_section("B站投稿"):
        settings["llm_api_key"] = config.get("B站投稿", "大模型api_key", fallback="").strip()
        settings["llm_api_base"] = config.get("B站投稿", "大模型api_base", fallback="https://api.minimaxi.com/anthropic").strip()
        settings["llm_model"] = config.get("B站投稿", "大模型model", fallback="MiniMax-M3").strip()

    if config.has_section("飞书文档配置"):
        settings["feishu_enabled"] = config.get("飞书文档配置", "启用飞书文档", fallback="是").strip() in {"是", "true", "1", "yes"}
        settings["feishu_app_id"] = config.get("飞书文档配置", "飞书app_id", fallback="").strip()
        settings["feishu_app_secret"] = config.get("飞书文档配置", "飞书app_secret", fallback="").strip()
        settings["feishu_folder_token"] = config.get("飞书文档配置", "飞书文件夹_token", fallback="").strip()
        settings["feishu_wiki_space_id"] = config.get("飞书文档配置", "飞书知识库space_id", fallback="").strip()
        settings["feishu_wiki_root_node"] = config.get("飞书文档配置", "飞书知识库根节点", fallback="").strip()
        settings["feishu_wiki_domain"] = config.get("飞书文档配置", "飞书域名", fallback="my.feishu.cn").strip()

    return settings


def extract_note_video_url(card: Dict[str, Any]) -> str:
    """從 note_card 取出可下載的視頻連結。

    小紅書的 stream 有兩種鍵名：舊版用編碼名（h264/h265/av1），
    新版用 EF4/EF5/EF6/EF7。故不依賴鍵名，一律掃描所有值，
    並優先挑「含音軌」（有 audio_codec / audio_bitrate）的合流，
    因為後續要抽音訊轉寫；找不到合流才退而取任一個有 master_url 的流。
    """
    media_stream = (card.get("video") or {}).get("media", {}).get("stream", {}) or {}
    muxed_url, any_url = "", ""
    for streams in media_stream.values():
        if not isinstance(streams, list):
            continue
        for st in streams:
            if not isinstance(st, dict):
                continue
            url = st.get("master_url")
            if not url:
                continue
            if not any_url:
                any_url = url
            if st.get("audio_codec") or st.get("audio_bitrate"):
                return url
    return any_url


class XhsNoteMonitor:
    def __init__(self, cookie: str = ""):
        self.cookie_str = cookie.strip()
        self.cookie_dict = self._parse_cookie_dict(self.cookie_str)
        self.signer = Xhshow() if Xhshow else None
        self.client = httpx.Client(
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Origin": "https://www.xiaohongshu.com",
            },
            follow_redirects=True,
            timeout=15.0
        )

    @staticmethod
    def _parse_cookie_dict(cookie_str: str) -> Dict[str, str]:
        cookies = {}
        for item in cookie_str.split(";"):
            if "=" in item:
                k, v = item.strip().split("=", 1)
                cookies[k.strip()] = v.strip()
        return cookies

    def _check_risk_control(self, response: httpx.Response, endpoint: str = ""):
        """嚴格校驗請求是否命中風控或人機驗證碼"""
        # 1. HTTP 461 / 471 驗證碼 (小紅書最具特徵的風控狀態碼)
        if response.status_code in (461, 471):
            verify_type = response.headers.get("Verifytype", "未知類型")
            verify_uuid = response.headers.get("Verifyuuid", "未知UUID")
            raise XhsRiskControlError(
                f"觸發小紅書人機滑塊驗證碼 (HTTP {response.status_code}, Verifytype: {verify_type}, Verifyuuid: {verify_uuid})"
            )

        # 2. 響應頭帶有驗證碼標識
        if "Verifyuuid" in response.headers or "verify" in response.headers.get("Content-Type", ""):
            raise XhsRiskControlError(f"響應頭出現驗證碼攔截標記: {response.headers.get('Verifyuuid')}")

        # 3. 伺服器拒絕或頻率超限
        if response.status_code in (403, 429):
            raise XhsRiskControlError(f"伺服器拒絕訪問或頻率超限 (HTTP {response.status_code})")

        # 4. 業務狀態碼檢查
        if response.status_code == 200:
            try:
                data = response.json()
            except Exception:
                # 200 卻回非 JSON，通常是 HTML 攔截頁/驗證頁的軟攔截。
                # 不可靜默放行，否則帳號失效或風控會被當成「本次無新內容」。
                snippet = (response.text or "")[:120].replace("\n", " ").strip()
                raise XhsRiskControlError(f"回應非 JSON（疑似驗證頁或攔截頁）: {snippet}")

            code = data.get("code")
            msg = str(data.get("msg", ""))

            # -100：登入態失效（Cookie 過期或被登出），需人工更新，不可靜默忽略
            if code == -100:
                raise XhsRiskControlError(f"小紅書登入態失效 (code=-100, msg={msg})，請更新 Cookie")

            # 300012: 訪問過於頻繁; 300015: 風控攔截
            if code in (300012, 300015) or "频繁" in msg or "验证" in msg or "风控" in msg:
                raise XhsRiskControlError(f"業務接口回傳風控阻斷: code={code}, msg={msg}")

    def parse_user_id(self, url: str) -> str:
        """從用戶個人主頁 URL 提取 user_id"""
        url = url.strip()
        if len(url) == 24 and re.match(r"^[a-f0-9]{24}$", url):
            return url
        m = re.search(r"user/profile/([a-f0-9]{24})", url)
        if m:
            return m.group(1)
        raise ValueError(f"無法從小紅書網址提取 24 位 user_id: {url}")

    def fetch_user_notes(self, user_id: str, count: int = 15) -> List[Dict[str, Any]]:
        """獲取博主最新筆記清單"""
        if not self.cookie_str or "a1" not in self.cookie_dict:
            logger.warning(
                "未檢測到有效的小紅書 Cookie！\n"
                "提示：小紅書接口強制要求登入憑證。\n"
                "請在 config.ini 的 [Cookie] 欄位填入：小红书cookie = a1=...; web_session=..."
            )
            return []

        if not self.signer:
            logger.error("未安裝 xhshow 簽名庫，請執行 pip install xhshow")
            return []

        uri = "/api/sns/web/v1/user_posted"
        full_url = f"https://edith.xiaohongshu.com{uri}"
        params = {
            "num": str(count),
            "cursor": "",
            "user_id": user_id,
            "image_scenes": "FD_WM_WEBP"
        }

        signed_headers = self.signer.sign_headers_get(
            uri=uri,
            cookies=self.cookie_dict,
            params=params
        )

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Cookie": self.cookie_str,
            "Referer": f"https://www.xiaohongshu.com/user/profile/{user_id}",
            "Origin": "https://www.xiaohongshu.com",
        }
        headers.update(signed_headers)

        resp = self.client.get(full_url, params=params, headers=headers)
        self._check_risk_control(resp, endpoint=uri)

        data = resp.json()
        if not data.get("success"):
            logger.error(f"小紅書接口返回失敗: {data.get('msg')} (code: {data.get('code')})")
            return []

        raw_notes = data.get("data", {}).get("notes", [])
        parsed_list = []

        for item in raw_notes:
            note_id = item.get("note_id") or item.get("id", "")
            title = item.get("display_title") or item.get("title", "")
            note_type = item.get("type", "normal")
            user_info = item.get("user", {})
            author_name = user_info.get("nickname") or user_info.get("nick_name", "小紅書博主")

            interact = item.get("interact_info", {})
            liked_count = interact.get("liked_count", "0")
            cover_info = item.get("cover", {})
            cover_url = cover_info.get("url_default") or cover_info.get("url", "")

            parsed_list.append({
                "note_id": note_id,
                "title": title or f"筆記_{note_id}",
                "note_type": note_type,
                "author": author_name,
                "liked_count": liked_count,
                "cover_url": cover_url,
                "xsec_token": item.get("xsec_token", ""),
            })

        return parsed_list

    def fetch_note_detail(self, note_id: str, xsec_token: str = "") -> Optional[Dict[str, Any]]:
        """獲取單篇筆記的完整正文文本與高清原圖/視頻流"""
        if not self.cookie_str or not self.signer:
            return None

        uri = "/api/sns/web/v1/feed"
        full_url = f"https://edith.xiaohongshu.com{uri}"
        payload = {
            "source_note_id": note_id,
            "extra": {"need_body_topic": "1"},
            "xsec_token": xsec_token
        }

        signed_headers = self.signer.sign_headers_post(
            uri=uri,
            cookies=self.cookie_dict,
            payload=payload
        )

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Cookie": self.cookie_str,
            "Referer": f"https://www.xiaohongshu.com/explore/{note_id}",
            "Origin": "https://www.xiaohongshu.com",
            "Content-Type": "application/json;charset=UTF-8"
        }
        headers.update(signed_headers)

        resp = self.client.post(full_url, json=payload, headers=headers)
        self._check_risk_control(resp, endpoint=uri)

        data = resp.json()
        items = data.get("data", {}).get("items", [])
        if not items:
            return None

        card = items[0].get("note_card", {})
        title = card.get("title", "")
        desc = card.get("desc", "")
        create_time = card.get("time", int(time.time() * 1000))
        dt_str = datetime.datetime.fromtimestamp(create_time / 1000.0).strftime("%Y-%m-%d %H:%M:%S")

        image_list = []
        for img in card.get("image_list", []):
            url = img.get("url_default") or img.get("url", "")
            if url:
                image_list.append(url)

        video_url = extract_note_video_url(card)

        return {
            "note_id": note_id,
            "title": title,
            "desc": desc,
            "publish_time": dt_str,
            "image_list": image_list,
            "video_url": video_url,
            "note_type": card.get("type", "normal")
        }

    def download_video(self, video_url: str, save_path: str, max_retries: int = 2) -> bool:
        """下載小紅書視頻。小紅書 CDN 會檢查 Referer，缺少時回 403。

        CDN 下載為靜態資源（不走業務 API，不觸發帳號風控）。
        長視頻下載容易因 CDN 網路抖動出現 RemoteProtocolError 等連線中斷，
        故內建短線重試機制（預設最多 2 次）。
        """
        if not video_url:
            logger.error("視頻下載連結為空")
            return False

        os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
        temp_path = save_path + ".tmp"
        headers = {
            "User-Agent": self.client.headers.get("User-Agent", ""),
            "Referer": "https://www.xiaohongshu.com/",
        }

        for attempt in range(1, max_retries + 1):
            logger.info(f"開始下載視頻 (第 {attempt}/{max_retries} 次) -> {save_path}")
            try:
                with self.client.stream("GET", video_url, headers=headers) as r:
                    r.raise_for_status()
                    total = int(r.headers.get("content-length", 0) or 0)
                    downloaded = 0
                    last_log = time.time()
                    with open(temp_path, "wb") as f:
                        for chunk in r.iter_bytes(chunk_size=65536):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if time.time() - last_log > 5:
                                mb = downloaded / (1024 * 1024)
                                if total > 0:
                                    logger.info(f"下載進度: {mb:.1f}MB / {total / (1024 * 1024):.1f}MB")
                                else:
                                    logger.info(f"已下載: {mb:.1f}MB")
                                last_log = time.time()

                if total > 0 and downloaded < total:
                    raise IOError(f"下載不完整：{downloaded}/{total} 位元組")

                os.replace(temp_path, save_path)
                logger.info(f"視頻下載完成: {save_path} ({os.path.getsize(save_path) / (1024 * 1024):.1f}MB)")
                return True
            except Exception as e:
                logger.warning(f"視頻下載異常 (第 {attempt}/{max_retries} 次): {type(e).__name__}: {e}")
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
                if attempt < max_retries:
                    time.sleep(2.0)

        logger.error(f"視頻下載最終失敗 ({max_retries} 次重試均未成功): {save_path}")
        return False


def load_urls_from_config(config_file: str) -> List[Dict[str, str]]:
    """從 xhs_URL_config.ini 讀取博主主頁列表"""
    if not os.path.exists(config_file):
        return []

    targets = []
    with open(config_file, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            parts = [p.strip() for p in line.split(",")]
            url = parts[0]
            name = ""
            for part in parts[1:]:
                if part.startswith("博主:") or part.startswith("博主："):
                    name = part.split(":", 1)[-1].strip()
                elif part.startswith("主播:") or part.startswith("主播："):
                    name = part.split(":", 1)[-1].strip()

            if url:
                targets.append({"url": url, "name": name})

    return targets


def render_photo_note_html(
    note_detail: Dict[str, Any],
    author: str,
    summary_text: str = ""
) -> str:
    """將小紅書圖文筆記渲染為純文字精編 HTML（不抓取、不渲染圖片，專注文字沉浸閱讀）"""
    title = note_detail.get("title", "")
    desc = note_detail.get("desc", "")
    publish_time = note_detail.get("publish_time", "")

    # 構造正文段落（純文字排版，無圖片防盜鏈破碎圖標干擾）
    desc_html_list = []
    for line in desc.splitlines():
        line = line.strip()
        if line:
            desc_html_list.append(f"<p>{render_markdown_inline(line)}</p>")
    desc_html = "\n".join(desc_html_list)

    # 構造摘要卡片
    summary_card = ""
    if summary_text:
        summary_card = f"""<div class="summary-card">
          <p><strong>💡 筆記核心精華提煉</strong></p>
          <p>{render_markdown_inline(summary_text)}</p>
        </div>"""

    body_html = f"""
    <div style="margin-top: 12px; font-size: 16px; line-height: 1.85;">
      {desc_html}
    </div>
    """

    now_str = time.strftime("%Y-%m-%d %H:%M")
    return HTML_TEMPLATE.format(
        page_title=f"{title or author} - 小紅書筆記精編",
        author=author or "小紅書博主",
        publish_time=publish_time or "近期",
        article_title=title or "小紅書圖文筆記",
        summary_html_card=summary_card,
        article_body_html=body_html,
        generate_time=now_str
    )


def process_video_note(monitor, detail: Dict[str, Any], author_name: str, title: str,
                       note_id: str, outdir: str, transcribe_script: str) -> Tuple[Optional[str], bool]:
    """影片筆記：下載視頻 → 抽 MP3 → 轉寫為 Markdown。

    回傳 (transcript_md_path, download_ok)：
    - 若影片下載或抽音訊失敗：回傳 (None, False)。呼叫端應中斷本篇處理，不發降級
      文檔，也不記入 history，留待下次巡檢重試（避免長視頻因 CDN 抖動被降級廢棄）。
    - 若音訊下載提取成功：回傳 (md_path, True)；即便音訊過短/無人聲導致轉寫失敗，
      md_path 雖為 None 但 download_ok 仍為 True，可安全降級以文案備份，不反覆重試。
    """
    from douyin_video_monitor import DouyinVideoMonitor

    safe_author = sanitize_filename_part(author_name, max_len=20) or "小紅書博主"
    media_dir = os.path.join(outdir, f"{safe_author}_作品")
    os.makedirs(media_dir, exist_ok=True)
    date_prefix = detail["publish_time"][:10].replace("-", "")
    clean_title = sanitize_filename_part(title, max_len=25) or note_id
    base = f"xhs_{date_prefix}_{clean_title}_{note_id}"
    mp4_path = os.path.join(media_dir, base + ".mp4")
    mp3_path = os.path.join(media_dir, base + ".mp3")

    if not monitor.download_video(detail["video_url"], mp4_path):
        return None, False

    if not DouyinVideoMonitor.extract_mp3_from_mp4(mp4_path, mp3_path):
        return None, False

    # 不保留視頻本體：與抖音作品一致，僅留 MP3 與 Markdown 供後續備份
    if os.path.exists(mp4_path):
        try:
            os.remove(mp4_path)
            logger.info(f"已清理暫存視頻，僅保留音訊與 Markdown: {mp4_path}")
        except OSError as e:
            logger.warning(f"刪除 MP4 失敗: {e}")

    if not transcribe_script:
        logger.warning("未配置 [录制设置] mp3转写md脚本路径，跳過影片轉寫")
        return None, True

    md_path = DouyinVideoMonitor.transcribe_mp3_to_md(os.path.abspath(mp3_path), transcribe_script)
    return md_path, True


def build_note_feishu_markdown(detail: Dict[str, Any], author_name: str, title: str,
                               liked_count: str, transcript_md_path: Optional[str] = None) -> str:
    """組出要匯入飛書的 Markdown。

    影片筆記有轉寫結果時，直接採用逐字稿（含 AI 提煉看點）；
    否則（圖文，或影片轉寫失敗）以標題、正文與配圖組成。
    """
    header = [
        f"# {title}",
        "",
        f"> 📕 **作者**：{author_name}  |  📅 **发布时间**：{detail['publish_time']}  |  👍 **点赞**：{liked_count}",
        "",
    ]

    if transcript_md_path and os.path.exists(transcript_md_path):
        try:
            with open(transcript_md_path, "r", encoding="utf-8") as f:
                body = f.read().strip()
            # 逐字稿自帶 H1 標題，改用統一的飛書標題，避免重複
            body = re.sub(r'^#\s+.*$', '', body, count=1, flags=re.MULTILINE).strip()
            return "\n".join(header) + "\n" + body + "\n"
        except Exception as e:
            logger.warning(f"讀取轉寫 Markdown 失敗，回退為圖文內容: {e}")

    parts = list(header)
    for img_url in detail.get("image_list", []):
        parts.append(f"![]({img_url})")
        parts.append("")
    parts.append(detail.get("desc", ""))
    return "\n".join(parts)


def build_note_doc_title(publish_time: str, author: str, note_title: str,
                         transcript_md_path: Optional[str] = None) -> str:
    """組出飛書文檔標題：`{日期}【{作者}】{主題}`。

    影片筆記有轉寫時，優先採用轉寫 Markdown 的 H1（AI 提煉標題），
    與抖音作品的做法一致；圖文筆記或轉寫失敗時，用筆記本身的標題。
    """
    topic = ""
    if transcript_md_path:
        try:
            from md_to_html import extract_article_title
            topic = extract_article_title(transcript_md_path)
        except Exception as e:
            logger.warning(f"讀取轉寫標題失敗，改用筆記標題: {e}")
    topic = topic or note_title or "未命名"
    return f"{publish_time[:10]}【{author}】{topic[:40]}"


def note_id_timestamp(note_id: str) -> Optional[float]:
    """由小紅書 note_id 解出建立時間（秒）。

    note_id 前 8 位十六進位即 Unix 時間戳，已用 15 篇已下載紀錄驗證 15/15 相符
    （例：6ad06ef7… → 2026-10-15 14:13）。因此判斷筆記新舊**不需要**抓詳情。

    解不出來或不合理時回傳 None，交由呼叫方照常處理——寧可多抓一篇，
    也不可在小紅書改動 ID 格式後把新筆記靜默濾掉。
    """
    if not note_id or len(note_id) < 8:
        return None
    try:
        ts = float(int(note_id[:8], 16))
    except (ValueError, TypeError):
        return None
    if ts < _NOTE_ID_TS_MIN or ts > time.time() + 86400:
        return None                    # 格式變更或時鐘異常 → 不判斷
    return ts


def is_note_out_of_window(note_id: str, since_days: float) -> bool:
    """筆記是否已超出時間窗口。since_days <= 0 或解不出日期時回傳 False（不限制）。"""
    if not since_days or since_days <= 0:
        return False
    ts = note_id_timestamp(note_id)
    if ts is None:
        return False
    return ts < time.time() - since_days * 86400


def load_history(filepath: str) -> set:
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("downloaded_ids", []))
        except Exception as e:
            logger.warning(f"讀取歷史紀錄失敗: {e}")
    return set()


def save_history(filepath: str, downloaded_ids: set):
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump({"downloaded_ids": sorted(list(downloaded_ids))}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存歷史紀錄失敗: {e}")


def load_retry_queue(filepath: str) -> Dict[str, int]:
    """讀取「下載失敗待重試」清單：{note_id: 已失敗次數}。

    沒有這份紀錄時，一封下載失敗的影片筆記不會寫入 history，看似留待下次重試；
    但下次巡檢它往往已超出 --days 時間窗口而在下載前被跳過，等於永久靜默丟失。
    """
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {str(k): int(v) for k, v in (data.get("retry", {}) or {}).items()}
        except Exception as e:
            logger.warning(f"讀取重試清單失敗: {e}")
    return {}


def save_retry_queue(filepath: str, retry: Dict[str, int]):
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    try:
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump({"retry": retry}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"保存重試清單失敗: {e}")


def order_notes_for_retry(notes: List[Dict[str, Any]],
                          retry_queue: Dict[str, int]) -> List[Dict[str, Any]]:
    """重試中的筆記排最前面，其餘維持原本（新到舊）順序。"""
    return sorted(notes, key=lambda n: n.get("note_id") not in retry_queue)


def process_monitor_once(
    args,
    monitor: XhsNoteMonitor,
    config_settings: Dict[str, Any],
    cooldown_mgr: CooldownManager
):
    """執行一次巡檢，具備風控熔斷檢查與隨機抖動延遲"""
    # 1. 檢查是否處於風控冷卻期
    is_cooling, remain_hours, cool_reason = cooldown_mgr.check_cooldown()
    if is_cooling:
        logger.warning(
            f"⛔ [風控熔斷保護生效中] 系統目前處於休眠狀態（尚餘 {remain_hours:.1f} 小時）。\n"
            f"觸發原因: {cool_reason}。\n"
            f"本輪巡檢已主動終止，保護帳號與 IP 安全。"
        )
        return

    targets = []
    if args.url:
        targets.append({"url": args.url, "name": ""})
    else:
        cfg_path = args.url_config or os.path.join(_CURRENT_DIR, "config", "xhs_URL_config.ini")
        targets = load_urls_from_config(cfg_path)
        if not targets:
            logger.error(f"未指定 --url 且未在 {cfg_path} 找到有效的博主配置")
            return

    history = load_history(args.history_file)
    retry_queue = load_retry_queue(args.retry_file)
    dingtalk_webhook = args.dingtalk_webhook or config_settings.get("dingtalk_webhook", "")
    dingtalk_phone = config_settings.get("dingtalk_phone", "")
    dingtalk_is_atall = config_settings.get("dingtalk_is_atall", False)
    transcribe_script = config_settings.get("transcribe_script", "")
    outdir = getattr(args, "outdir", "./downloads")

    for t_idx, target in enumerate(targets, 1):
        url = target["url"]
        custom_name = target["name"]

        # 博主之間至少間隔 10 分鐘（隨機 10~15 分鐘），防止批量抓取特徵
        if t_idx > 1:
            jitter = random.uniform(600.0, 900.0)
            logger.info(f"防風控隨機延遲: 休眠 {jitter / 60:.1f} 分鐘後檢查下一位博主...")
            time.sleep(jitter)

        try:
            user_id = monitor.parse_user_id(url)
            logger.info(f"開始檢查小紅書博主: 【{custom_name or user_id}】")

            notes = monitor.fetch_user_notes(user_id, count=args.count)
            logger.info(f"取得作品筆記清單: {len(notes)} 篇")

            max_new = getattr(args, "max_new", 0) or 0
            since_days = getattr(args, "days", 1.5)
            processed_this_author = 0
            skipped_old = 0

            # 重試清單裡的筆記優先處理，且不受 --days 窗口限制：
            # 一封「今天發現、下載失敗」的筆記到了下次巡檢可能已超出窗口，
            # 若仍套用窗口就會在重試前被跳過，等於永久靜默丟失。
            for n in order_notes_for_retry(notes, retry_queue):
                note_id = n["note_id"]
                retrying = note_id in retry_queue
                if not note_id or note_id in history:
                    continue

                # 超出時間窗口的舊筆記直接跳過。note_id 自帶建立時間，判斷不需抓詳情，
                # 所以跳過是零成本的：不發請求、不 sleep、也不佔 max_new 額度。
                # 沒有這道過濾時，新增博主會沿著歷史往回抓（實際發生過抓到 200 天前）。
                if not retrying and is_note_out_of_window(note_id, since_days):
                    skipped_old += 1
                    continue

                # 限制單次每位博主最多處理幾篇新筆記；未處理的留待下次巡檢。
                # 重試中的筆記不佔額度，否則它們可能永遠排不到。
                if not retrying and max_new > 0 and processed_this_author >= max_new:
                    logger.info(
                        f"已達單次處理上限 {max_new} 篇，其餘新筆記留待下次巡檢: 【{custom_name or user_id}】"
                    )
                    break

                # 筆記詳情抓取至少間隔 3 分鐘（隨機 3~5 分鐘）
                detail_jitter = random.uniform(180.0, 300.0)
                logger.info(f"防風控隨機延遲: 休眠 {detail_jitter / 60:.1f} 分鐘後抓取下一篇筆記...")
                time.sleep(detail_jitter)

                detail = monitor.fetch_note_detail(note_id, xsec_token=n.get("xsec_token", ""))
                if not detail:
                    continue

                author_name = custom_name or n["author"] or "小紅書博主"
                title = detail["title"] or n["title"]
                logger.info(f"發現新筆記: 【{author_name}】{title}")

                read_url = ""
                # 如果是圖文筆記：生成圖文 HTML
                if detail["note_type"] == "normal" and config_settings.get("web_enabled", True):
                    web_outdir = config_settings.get("web_outdir", "./web_posts")
                    web_base_url = config_settings.get("web_base_url", "http://101.35.47.249:8080/posts").rstrip("/")

                    date_prefix = detail["publish_time"][:10].replace("-", "")
                    clean_title = sanitize_filename_part(title, max_len=25) or note_id
                    html_filename = f"xhs_{date_prefix}_{clean_title}_{note_id}.html"
                    html_path = os.path.join(web_outdir, html_filename)

                    html_content = render_photo_note_html(detail, author=author_name)
                    os.makedirs(web_outdir, exist_ok=True)
                    with open(html_path, "w", encoding="utf-8") as hf:
                        hf.write(html_content)

                    logger.info(f"成功生成小紅書圖文網頁: {html_path}")
                    quoted_name = urllib.parse.quote(html_filename)
                    read_url = f"{web_base_url}/index.html#/{quoted_name}"
                    try:
                        from web_portal import update_portal
                        update_portal(web_outdir)
                    except Exception as pe:
                        logger.warning(f"更新閱讀館門戶索引失敗: {pe}")

                # 影片筆記：下載 → 抽 MP3 → 轉寫 → Markdown（與抖音作品同一條鏈路）
                transcript_md_path = None
                if detail["note_type"] == "video" and detail.get("video_url"):
                    transcript_md_path, download_ok = process_video_note(
                        monitor, detail, author_name, title, note_id, outdir, transcribe_script
                    )
                    if not download_ok:
                        # 視頻下載失敗（CDN 網路抖動等）：不降級發送半成品，也不記入歷史。
                        # 但只靠「下次巡檢重試」不夠——下次巡檢時它可能已超出 --days 窗口
                        # 而被跳過，所以這裡必須寫入重試清單，讓它繞過窗口限制。
                        attempts = retry_queue.get(note_id, 0) + 1
                        if attempts >= _XHS_MAX_DOWNLOAD_ATTEMPTS:
                            logger.error(
                                f"影片下載連續 {attempts} 次失敗，放棄並記入歷史: 【{author_name}】{title}"
                            )
                            retry_queue.pop(note_id, None)
                            history.add(note_id)
                            save_history(args.history_file, history)
                        else:
                            retry_queue[note_id] = attempts
                            logger.warning(
                                f"影片下載失敗（第 {attempts}/{_XHS_MAX_DOWNLOAD_ATTEMPTS} 次），"
                                f"已排入重試清單，下次巡檢優先重試: 【{author_name}】{title}"
                            )
                        save_retry_queue(args.retry_file, retry_queue)
                        continue

                    if not transcript_md_path:
                        logger.warning(
                            f"影片轉寫未完成（音訊無人聲或極短），飛書文檔將安全降級以封面與文案生成: {title[:30]}"
                        )

                # 生成飛書雲文檔（影片有逐字稿則用逐字稿；否則用標題、正文與配圖）
                feishu_url = ""
                if config_settings.get("feishu_enabled", True) and config_settings.get("feishu_app_id"):
                    try:
                        from feishu_uploader import FeishuDocManager
                        feishu_mgr = FeishuDocManager(
                            app_id=config_settings.get("feishu_app_id", ""),
                            app_secret=config_settings.get("feishu_app_secret", ""),
                            folder_token=config_settings.get("feishu_folder_token", ""),
                            wiki_space_id=config_settings.get("feishu_wiki_space_id", ""),
                            wiki_root_node=config_settings.get("feishu_wiki_root_node", ""),
                            wiki_domain=config_settings.get("feishu_wiki_domain", "my.feishu.cn"),
                        )
                        note_md = build_note_feishu_markdown(
                            detail, author_name, title, n.get("liked_count", "0"),
                            transcript_md_path=transcript_md_path,
                        )
                        note_md_path = os.path.join("/tmp", f"xhs_feishu_{note_id}.md")
                        with open(note_md_path, "w", encoding="utf-8") as nf:
                            nf.write(note_md)

                        # 影片筆記有轉寫時優先採用 AI 提煉標題（與抖音作品一致）
                        doc_title = build_note_doc_title(
                            detail['publish_time'], author_name, title,
                            transcript_md_path=transcript_md_path,
                        )
                        doc_url, _ = feishu_mgr.create_article_docx(
                            note_md_path, author=author_name, doc_title=doc_title
                        )
                        if os.path.exists(note_md_path):
                            os.remove(note_md_path)
                        if doc_url:
                            feishu_url = doc_url
                            logger.info(f"成功創建飛書雲文檔: {feishu_url}")
                    except Exception as fe:
                        logger.error(f"創建飛書雲文檔異常: {fe}")

                # 釘釘通知
                if dingtalk_webhook:
                    links = []
                    if feishu_url:
                        links.append(f"📄 飞书云文档（划线与收藏）：\n{feishu_url}")
                    if read_url:
                        links.append(f"📖 手机在线图文精编：\n{read_url}")
                    link_sec = "\n\n".join(links) + "\n\n" if links else ""
                    msg_content = (
                        f"📕 【小红书更新】 【{author_name}】\n"
                        f"标题：{title}\n"
                        f"{link_sec}"
                        f"类型：{'圖文筆記' if detail['note_type'] == 'normal' else '視頻筆記'}\n"
                        f"发布时间：{detail['publish_time']}\n"
                        f"点赞：{n.get('liked_count', '0')}"
                    )
                    msg_push.dingtalk(dingtalk_webhook, msg_content, dingtalk_phone, dingtalk_is_atall)
                    logger.info("釘釘推送成功")

                history.add(note_id)
                save_history(args.history_file, history)
                if retry_queue.pop(note_id, None) is not None:
                    save_retry_queue(args.retry_file, retry_queue)
                # 重試筆記不佔用本次的新筆記額度（與上面的 max_new 豁免保持一致）
                if not retrying:
                    processed_this_author += 1

            if skipped_old:
                logger.info(
                    f"略過 {skipped_old} 篇超出 {since_days} 天窗口的舊筆記（不抓詳情、不佔額度）: "
                    f"【{custom_name or user_id}】"
                )

        except XhsRiskControlError as rce:
            # 捕獲風控異常，立即激活 24 小時冷卻熔斷
            cooldown_mgr.activate_cooldown(
                hours=24.0,
                reason=str(rce),
                dingtalk_webhook=dingtalk_webhook,
                dingtalk_phone=dingtalk_phone,
                dingtalk_is_atall=dingtalk_is_atall,
                feishu_webhook=config_settings.get("feishu_webhook", ""),
                login_url=xhs_login_url(),
            )
            break
        except Exception as e:
            logger.error(f"檢查小紅書博主 {url} 發生異常: {e}")


def main():
    parser = argparse.ArgumentParser(description="小紅書博主作品監控與精編流水線 (高強度風控防護版)")
    parser.add_argument("--url", type=str, default="", help="單個小紅書主頁 URL 或 24 位 user_id")
    parser.add_argument("--url-config", type=str, default="config/xhs_URL_config.ini", help="小紅書博主列表設定檔")
    parser.add_argument("--cookie", type=str, default="", help="小紅書測試小號 Cookie (若不填則讀取 config.ini)")
    parser.add_argument("--count", type=int, default=15, help="查詢最新作品數量 (預設 15 部)")
    parser.add_argument(
        "--max-new", type=int, default=0,
        help="單次每位博主最多處理幾篇新筆記 (0 表示不限制)；未處理的留待下次巡檢，不會遺漏"
    )
    parser.add_argument(
        "--days", type=float, default=1.5,
        help="只處理過去幾天內發布的筆記 (預設 1.5 天 = 36 小時)；0 表示不限制。"
             "舊筆記由 note_id 自帶的時間戳判斷，不需抓詳情即可跳過"
    )
    parser.add_argument("--history-file", type=str, default="./downloads/xhs_download_history.json", help="去重歷史紀錄檔案")
    parser.add_argument("--retry-file", type=str, default="./downloads/xhs_retry_queue.json", help="下載失敗待重試清單")
    parser.add_argument("--outdir", type=str, default="./downloads", help="影片/音訊保存目錄（影片筆記轉寫用）")
    parser.add_argument("--cooldown-file", type=str, default="./downloads/xhs_cooldown.json", help="風控熔斷記錄檔案")
    parser.add_argument("--daemon", action="store_true", help="啟用常駐輪詢模式 (預設 12 小時檢查一次)")
    parser.add_argument("--interval-hours", type=float, default=12.0, help="常駐檢查間隔小時數")
    parser.add_argument("--dingtalk-webhook", type=str, default="", help="釘釘推送 Webhook")

    args = parser.parse_args()

    config = load_project_config()
    cookie_str = args.cookie or config.get("xhs_cookie", "")

    cooldown_mgr = CooldownManager(filepath=args.cooldown_file)
    monitor = XhsNoteMonitor(cookie=cookie_str)

    if args.daemon:
        interval_secs = int(args.interval_hours * 3600)
        logger.info(f"啟動小紅書常駐防風控輪詢模式，每 {args.interval_hours} 小時檢查一次...")
        while True:
            try:
                process_monitor_once(args, monitor, config, cooldown_mgr)
            except Exception as e:
                logger.error(f"本次輪詢異常: {e}")
            logger.info(f"等待下次輪詢 ({args.interval_hours} 小時後)...")
            time.sleep(interval_secs)
    else:
        process_monitor_once(args, monitor, config, cooldown_mgr)


if __name__ == "__main__":
    main()
