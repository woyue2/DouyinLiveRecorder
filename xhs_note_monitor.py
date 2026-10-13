# -*- encoding: utf-8 -*-
"""
小紅書博主作品（圖文/視頻）監控與提煉模組
功能：
1. 自動解析小紅書博主個人主頁 URL 中的 user_id
2. 結合 xhshow 簽名庫與用戶 Cookie 抓取最新發布筆記清單
3. 自動識別筆記類型：
   - 圖文筆記 (normal)：直接提取標題、正文描述與高清配圖，調用 MiniMax 提煉看點並生成圖文 HTML
   - 視頻筆記 (video)：下載視頻 -> 提取 MP3 -> SenseVoice 轉寫 -> 生成 HTML
4. 釘釘推送卡片通知，附帶在線手機端精編閱讀短鏈
"""

import os
import re
import sys
import time
import json
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

    def parse_user_id(self, url: str) -> str:
        """從用戶個人主頁 URL 提取 user_id"""
        url = url.strip()
        if len(url) == 24 and re.match(r"^[a-f0-9]{24}$", url):
            return url
        m = re.search(r"user/profile/([a-f0-9]{24})", url)
        if m:
            return m.group(1)
        raise ValueError(f"無法從小紅書網址提取 24 位 user_id: {url}")

    def fetch_user_notes(self, user_id: str, count: int = 20) -> List[Dict[str, Any]]:
        """
        獲取博主最新筆記清單
        注意：小紅書接口強制要求有效登入 Cookie (a1 / web_session)
        """
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
        if resp.status_code != 200:
            logger.error(f"小紅書接口返回錯誤狀態碼: {resp.status_code}")
            return []

        data = resp.json()
        if not data.get("success"):
            logger.error(f"小紅書接口返回失敗: {data.get('msg')} (code: {data.get('code')})")
            return []

        raw_notes = data.get("data", {}).get("notes", [])
        parsed_list = []

        for item in raw_notes:
            note_id = item.get("note_id") or item.get("id", "")
            title = item.get("display_title") or item.get("title", "")
            note_type = item.get("type", "normal")  # normal (圖文) 或 video (視頻)
            user_info = item.get("user", {})
            author_name = user_info.get("nickname") or user_info.get("nick_name", "小紅書博主")

            # 封面與點贊
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
        if resp.status_code != 200:
            return None

        data = resp.json()
        items = data.get("data", {}).get("items", [])
        if not items:
            return None

        card = items[0].get("note_card", {})
        title = card.get("title", "")
        desc = card.get("desc", "")
        create_time = card.get("time", int(time.time() * 1000))
        dt_str = datetime.datetime.fromtimestamp(create_time / 1000.0).strftime("%Y-%m-%d %H:%M:%S")

        # 圖片清單
        image_list = []
        for img in card.get("image_list", []):
            url = img.get("url_default") or img.get("url", "")
            if url:
                image_list.append(url)

        # 視頻清單
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

    # 構造圖片瀑布流 HTML
    img_html_list = []
    for idx, img_url in enumerate(images, 1):
        img_html_list.append(
            f'<div style="margin: 16px 0; text-align: center;">'
            f'<img src="{img_url}" style="max-width: 100%; border-radius: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.08);" loading="lazy">'
            f'</div>'
        )
    gallery_html = "\n".join(img_html_list)

    # 構造正文段落
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


def main():
    parser = argparse.ArgumentParser(description="小紅書博主作品監控與精編流水線")
    parser.add_argument("--url", type=str, required=True, help="小紅書個人主頁 URL 或 24 位 user_id")
    parser.add_argument("--cookie", type=str, default="", help="小紅書登入 Cookie (若不填則讀取 config.ini)")
    args = parser.parse_args()

    config = load_project_config()
    cookie_str = args.cookie or config.get("xhs_cookie", "")

    monitor = XhsNoteMonitor(cookie=cookie_str)
    user_id = monitor.parse_user_id(args.url)
    logger.info(f"解析到小紅書 user_id: {user_id}")

    notes = monitor.fetch_user_notes(user_id, count=10)
    logger.info(f"取得作品筆記清單: {len(notes)} 篇")

    for idx, n in enumerate(notes, 1):
        type_str = "【視頻】" if n["note_type"] == "video" else "【圖文】"
        print(f"[{idx}] {type_str} ID: {n['note_id']} | 讚數: {n['liked_count']}")
        print(f"    標題: {n['title']}")
        print("-" * 60)


if __name__ == "__main__":
    main()
