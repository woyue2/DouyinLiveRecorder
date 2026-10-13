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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("XhsNoteMonitor")


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
        dingtalk_is_atall: bool = False
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

    settings = {
        "xhs_cookie": "",
        "transcribe_script": "",
        "dingtalk_webhook": "",
        "dingtalk_phone": "",
        "dingtalk_is_atall": False,
        "dingtalk_enabled": False,
        "web_enabled": True,
        "web_outdir": "./web_posts",
        "web_base_url": "http://101.35.47.249:8080/posts",
        "web_retention_days": 30,
        "llm_api_key": "",
        "llm_api_base": "https://api.minimaxi.com/anthropic",
        "llm_model": "MiniMax-M3",
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

    if config.has_section("Web服务配置"):
        settings["web_enabled"] = config.get("Web服务配置", "启用网页生成", fallback="是").strip() in {"是", "true", "1", "yes"}
        settings["web_outdir"] = config.get("Web服务配置", "网页输出目录", fallback="./web_posts").strip()
        settings["web_base_url"] = config.get("Web服务配置", "网页访问地址", fallback="http://101.35.47.249:8080/posts").strip()
        settings["web_retention_days"] = config.getint("Web服务配置", "网页保留天数", fallback=30)

    if config.has_section("B站投稿"):
        settings["llm_api_key"] = config.get("B站投稿", "大模型api_key", fallback="").strip()
        settings["llm_api_base"] = config.get("B站投稿", "大模型api_base", fallback="https://api.minimaxi.com/anthropic").strip()
        settings["llm_model"] = config.get("B站投稿", "大模型model", fallback="MiniMax-M3").strip()

    return settings


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

        # 3. 業務狀態碼檢查
        if response.status_code == 200:
            try:
                data = response.json()
                code = data.get("code")
                msg = str(data.get("msg", ""))
                # 300012: 訪問過於頻繁; 300015: 風控攔截; -100: 登入態失效
                if code in (300012, 300015) or "频繁" in msg or "验证" in msg or "风控" in msg:
                    raise XhsRiskControlError(f"業務接口回傳風控阻斷: code={code}, msg={msg}")
            except Exception:
                pass

        if response.status_code in (403, 429):
            raise XhsRiskControlError(f"伺服器拒絕訪問或頻率超限 (HTTP {response.status_code})")

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
            "image_scenes": ["CR_PRV", "CR_WM_WEBP"],
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

        video_info = card.get("video", {})
        video_url = ""
        media_stream = video_info.get("media", {}).get("stream", {})
        h264_streams = media_stream.get("h264", [])
        if h264_streams:
            video_url = h264_streams[0].get("master_url", "")

        return {
            "note_id": note_id,
            "title": title,
            "desc": desc,
            "publish_time": dt_str,
            "image_list": image_list,
            "video_url": video_url,
            "note_type": card.get("type", "normal")
        }


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
    """將小紅書圖文筆記直接渲染為手機端高顏值 HTML"""
    title = note_detail.get("title", "")
    desc = note_detail.get("desc", "")
    publish_time = note_detail.get("publish_time", "")
    images = note_detail.get("image_list", [])

    img_html_list = []
    for idx, img_url in enumerate(images, 1):
        img_html_list.append(
            f'<div style="margin: 16px 0; text-align: center;">'
            f'<img src="{img_url}" style="max-width: 100%; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.08);" loading="lazy">'
            f'</div>'
        )
    gallery_html = "\n".join(img_html_list)

    desc_html_list = []
    for line in desc.splitlines():
        line = line.strip()
        if line:
            desc_html_list.append(f"<p>{render_markdown_inline(line)}</p>")
    desc_html = "\n".join(desc_html_list)

    summary_card = ""
    if summary_text:
        summary_card = f"""<div class="summary-card">
          <p><strong>💡 筆記核心精華提煉</strong></p>
          <p>{render_markdown_inline(summary_text)}</p>
        </div>"""

    body_html = f"""
    {gallery_html}
    <div style="margin-top: 24px; font-size: 16px; line-height: 1.85;">
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
    dingtalk_webhook = args.dingtalk_webhook or config_settings.get("dingtalk_webhook", "")
    dingtalk_phone = config_settings.get("dingtalk_phone", "")
    dingtalk_is_atall = config_settings.get("dingtalk_is_atall", False)

    for t_idx, target in enumerate(targets, 1):
        url = target["url"]
        custom_name = target["name"]

        # 博主之間增加 5~12 秒隨機抖動延遲，防範批量爬蟲特徵
        if t_idx > 1:
            jitter = random.uniform(5.0, 12.0)
            logger.info(f"防風控隨機延遲: 休眠 {jitter:.1f} 秒後檢查下一位博主...")
            time.sleep(jitter)

        try:
            user_id = monitor.parse_user_id(url)
            logger.info(f"開始檢查小紅書博主: 【{custom_name or user_id}】")

            notes = monitor.fetch_user_notes(user_id, count=args.count)
            logger.info(f"取得作品筆記清單: {len(notes)} 篇")

            for n in notes:
                note_id = n["note_id"]
                if not note_id or note_id in history:
                    continue

                # 筆記詳情抓取間隔 2.5~6 秒隨機抖動
                detail_jitter = random.uniform(2.5, 6.0)
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
                    clean_title = re.sub(r'[\s#\\/*?:"<>|]', "_", title[:25]).strip("_") or note_id
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

                # 釘釘通知
                if dingtalk_webhook:
                    link_sec = f"📖 手机在线图文精编：\n{read_url}\n\n" if read_url else ""
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

        except XhsRiskControlError as rce:
            # 捕獲風控異常，立即激活 24 小時冷卻熔斷
            cooldown_mgr.activate_cooldown(
                hours=24.0,
                reason=str(rce),
                dingtalk_webhook=dingtalk_webhook,
                dingtalk_phone=dingtalk_phone,
                dingtalk_is_atall=dingtalk_is_atall
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
    parser.add_argument("--history-file", type=str, default="./downloads/xhs_download_history.json", help="去重歷史紀錄檔案")
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
