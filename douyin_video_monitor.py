# -*- encoding: utf-8 -*-
"""
抖音博主新作品監控與下載模組 (方案B：基於 a_bogus 原生接口解析)
完整流水線：
1. 自動解析抖音用戶主頁連結中的 sec_user_id (支援長連結與短連結)
2. 自動獲取免登入訪客 ttwid / UIFID Cookie
3. 繞過 ArgusSecurityPlugin 邊緣網關，抓取博主最近發布的作品清單
4. 依據日期/時間戳篩選新視頻（自動過濾圖文與置頂）
5. 下載無水印 MP4 -> ffmpeg 提取高品質 VBR MP3
6. 呼叫 SenseVoice / LLM 腳本轉寫為 Markdown (MP3 -> MD)
7. 提煉精華看點並推送釘釘通知 (DingTalk)
8. 對接百度網盤上傳 (bypy / upload_baidu.sh)
9. 支援常駐輪詢模式 (每 12 小時檢查一次)
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
from typing import List, Dict, Optional, Any

import httpx

# 確保可以載入 src 下的 ab_sign 與 msg_push
_CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
if _CURRENT_DIR not in sys.path:
    sys.path.insert(0, _CURRENT_DIR)
_SRC_DIR = os.path.join(_CURRENT_DIR, "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from ab_sign import ab_sign
import msg_push

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("DouyinVideoMonitor")

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def load_project_config() -> Dict[str, Any]:
    """讀取專案 config.ini 中的轉寫與推送設定"""
    config = configparser.RawConfigParser()
    possible_paths = [
        os.path.join(_CURRENT_DIR, "config", "config.ini"),
        os.path.join(_CURRENT_DIR, "config.ini"),
    ]
    loaded = False
    for p in possible_paths:
        if os.path.exists(p):
            config.read(p, encoding="utf-8-sig")
            loaded = True
            break

    settings = {
        "transcribe_script": "",
        "dingtalk_webhook": "",
        "dingtalk_phone": "",
        "dingtalk_is_atall": False,
        "dingtalk_enabled": False,
    }

    if not loaded:
        return settings

    # 轉寫腳本
    if config.has_section("录制设置"):
        settings["transcribe_script"] = config.get("录制设置", "mp3转写md脚本路径", fallback="").strip()

    # 推送設定
    if config.has_section("推送配置"):
        push_types = config.get("推送配置", "直播状态推送渠道", fallback="").strip()
        settings["dingtalk_webhook"] = config.get("推送配置", "钉钉推送接口链接", fallback="").strip()
        settings["dingtalk_phone"] = config.get("推送配置", "钉钉通知@对象(填手机号)", fallback="").strip()
        at_all_str = config.get("推送配置", "钉钉通知@全体(是/否)", fallback="否").strip()
        settings["dingtalk_is_atall"] = at_all_str in {"是", "true", "1", "yes"}
        settings["dingtalk_enabled"] = bool("钉钉" in push_types and settings["dingtalk_webhook"])

    return settings


class DouyinVideoMonitor:
    def __init__(self, user_agent: str = DEFAULT_UA, timeout: float = 15.0):
        self.ua = user_agent
        self.timeout = timeout
        self.client = httpx.Client(
            headers={"User-Agent": self.ua},
            follow_redirects=True,
            timeout=self.timeout
        )
        self._cookie_str: Optional[str] = None
        self._cookie_expire_at: float = 0

    def parse_sec_user_id(self, url_or_id: str) -> str:
        """從抖音用戶主頁連結提取 sec_user_id"""
        url_or_id = url_or_id.strip()
        if url_or_id.startswith("MS4wLjABAAAA") and "/" not in url_or_id:
            return url_or_id

        if "v.douyin.com" in url_or_id:
            try:
                resp = self.client.get(url_or_id)
                url_or_id = str(resp.url)
            except Exception as e:
                logger.warning(f"短連結跳轉失敗: {e}")

        match = re.search(r"user/([^/?]+)", url_or_id)
        if match:
            return match.group(1)

        match_param = re.search(r"sec_uid=([^&]+)", url_or_id)
        if match_param:
            return match_param.group(1)

        raise ValueError(f"無法從該網址提取 sec_user_id: {url_or_id}")

    def get_visitor_cookies(self, force_refresh: bool = False) -> str:
        """獲取訪客 Cookie (包含 ttwid 與 UIFID)"""
        now = time.time()
        if not force_refresh and self._cookie_str and now < self._cookie_expire_at:
            return self._cookie_str

        logger.info("正在取得訪客 Session Cookie (ttwid / UIFID)...")
        resp = self.client.get("https://live.douyin.com/1")
        cookie_dict = dict(resp.cookies)

        for k, v in self.client.cookies.items():
            cookie_dict[k] = v

        self._cookie_str = "; ".join([f"{k}={v}" for k, v in cookie_dict.items()])
        self._cookie_expire_at = now + 3600
        logger.info(f"成功取得訪客 Cookie: {list(cookie_dict.keys())}")
        return self._cookie_str

    def fetch_user_videos(self, sec_user_id: str, count: int = 15) -> List[Dict[str, Any]]:
        """獲取博主最新作品清單"""
        cookie_str = self.get_visitor_cookies()

        params = {
            "device_platform": "webapp",
            "aid": "6383",
            "channel": "channel_pc_web",
            "sec_user_id": sec_user_id,
            "max_cursor": "0",
            "count": str(count),
            "publish_video_strategy_type": "2",
            "update_version_code": "170400",
            "pc_client_type": "1",
            "version_code": "190500",
            "version_name": "19.5.0",
            "cookie_enabled": "true",
            "screen_width": "1920",
            "screen_height": "1080",
            "browser_language": "zh-CN",
            "browser_platform": "Win32",
            "browser_name": "Chrome",
            "browser_version": "120.0.0.0",
            "browser_online": "true",
        }

        query_str = urllib.parse.urlencode(params)
        bogus = ab_sign(query_str, self.ua)
        api_url = f"https://www.douyin.com/aweme/v1/web/aweme/post/?{query_str}&a_bogus={bogus}"

        headers = {
            "User-Agent": self.ua,
            "Referer": f"https://www.douyin.com/user/{sec_user_id}",
            "Accept": "application/json, text/plain, */*",
            "Cookie": cookie_str,
            "x-tt-argus": "1",
        }

        resp = self.client.get(api_url, headers=headers)
        if resp.status_code != 200:
            if "ArgusSecurityPlugin" in resp.text:
                logger.warning("遇到 ArgusSecurityPlugin 攔截，嘗試刷新 Cookie 重新請求...")
                cookie_str = self.get_visitor_cookies(force_refresh=True)
                headers["Cookie"] = cookie_str
                resp = self.client.get(api_url, headers=headers)
            resp.raise_for_status()

        data = resp.json()
        aweme_list = data.get("aweme_list", [])
        results = []

        for item in aweme_list:
            aweme_id = item.get("aweme_id", "")
            desc = item.get("desc", "").strip()
            create_time = item.get("create_time", 0)
            is_top = item.get("is_top", 0) == 1
            aweme_type = item.get("aweme_type", 0)
            is_video = (aweme_type == 0 and not bool(item.get("images")))

            video_info = item.get("video", {})
            duration_ms = video_info.get("duration", 0)
            duration_sec = duration_ms / 1000.0 if duration_ms > 0 else 0.0

            play_addr = video_info.get("play_addr", {})
            url_list = play_addr.get("url_list", [])
            video_url = url_list[0] if url_list else ""

            dt_str = datetime.datetime.fromtimestamp(create_time).strftime("%Y-%m-%d %H:%M:%S")

            results.append({
                "aweme_id": aweme_id,
                "title": desc or f"作品_{aweme_id}",
                "create_time": create_time,
                "publish_time": dt_str,
                "is_top": is_top,
                "is_video": is_video,
                "aweme_type": aweme_type,
                "duration_sec": duration_sec,
                "video_url": video_url,
                "author": item.get("author", {}).get("nickname", "未知博主"),
            })

        return results

    @staticmethod
    def filter_recent_videos(
        videos: List[Dict[str, Any]],
        since_days: Optional[float] = None,
        since_timestamp: Optional[float] = None,
        include_top: bool = True,
        video_only: bool = True
    ) -> List[Dict[str, Any]]:
        """過濾指定日期之後的影片，並可選擇排除圖文作品與置頂"""
        now = time.time()
        threshold_ts = since_timestamp

        if since_days is not None:
            threshold_ts = now - (since_days * 86400)

        filtered = []
        for v in videos:
            if video_only and not v.get("is_video", True):
                continue
            if not include_top and v["is_top"]:
                continue
            if threshold_ts is not None and v["create_time"] < threshold_ts:
                continue
            filtered.append(v)

        return filtered

    def download_video(self, video_url: str, save_path: str) -> bool:
        """下載無水印 MP4"""
        if not video_url:
            logger.error("下載連結為空")
            return False

        os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
        temp_path = save_path + ".tmp"

        logger.info(f"開始下載 MP4 -> {save_path}")
        try:
            with self.client.stream("GET", video_url) as r:
                r.raise_for_status()
                total = int(r.headers.get("content-length", 0))
                downloaded = 0
                last_log = time.time()

                with open(temp_path, "wb") as f:
                    for chunk in r.iter_bytes(chunk_size=65536):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if time.time() - last_log > 3:
                            mb = downloaded / (1024 * 1024)
                            if total > 0:
                                percent = (downloaded / total) * 100
                                logger.info(f"下載進度: {mb:.1f}MB / {total / (1024 * 1024):.1f}MB ({percent:.1f}%)")
                            else:
                                logger.info(f"已下載: {mb:.1f}MB")
                            last_log = time.time()

            os.replace(temp_path, save_path)
            logger.info(f"MP4 下載完成: {save_path} ({os.path.getsize(save_path) / (1024 * 1024):.1f}MB)")
            return True
        except Exception as e:
            logger.error(f"下載失敗: {e}")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return False

    @staticmethod
    def extract_mp3_from_mp4(mp4_path: str, mp3_path: str) -> bool:
        """使用 ffmpeg 將 MP4 音軌提取為 VBR 質量的 MP3"""
        if not os.path.exists(mp4_path):
            logger.error(f"MP4 檔案不存在: {mp4_path}")
            return False

        os.makedirs(os.path.dirname(os.path.abspath(mp3_path)), exist_ok=True)
        logger.info(f"正在提取 MP3: {mp4_path} -> {mp3_path}")

        cmd = [
            "ffmpeg",
            "-y",
            "-i", mp4_path,
            "-vn",
            "-c:a", "libmp3lame",
            "-q:a", "2",
            mp3_path
        ]

        try:
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
            logger.info(f"MP3 提取完成: {mp3_path} ({os.path.getsize(mp3_path) / (1024 * 1024):.2f}MB)")
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"ffmpeg 轉換失敗: {e.stderr.decode('utf-8', errors='ignore')}")
            return False

    @staticmethod
    def transcribe_mp3_to_md(mp3_path: str, script_path: str) -> Optional[str]:
        """
        呼叫轉寫腳本 (例如 /home/ubuntu/1mp3tomd/transcribe_one.sh)
        生成同名 .md 檔案（內含 AI 提煉摘要與逐字稿）
        """
        if not script_path or not os.path.exists(script_path):
            logger.warning(f"轉寫腳本不存在或未配置: {script_path}")
            return None

        placeholder = mp3_path + ".transcribing"
        try:
            with open(placeholder, "w") as f:
                f.write(str(time.time()))
        except Exception:
            pass

        logger.info(f"開始執行轉寫: bash {script_path} {mp3_path}")
        try:
            result = subprocess.run(
                ["bash", script_path, mp3_path],
                capture_output=True,
                text=True,
                timeout=3600
            )
            if result.returncode == 0:
                base_no_ext = os.path.splitext(mp3_path)[0]
                md_candidate = base_no_ext + ".md"
                sensitive_candidate = base_no_ext + "-敏感.md"

                if os.path.exists(md_candidate):
                    logger.info(f"轉寫成功，生成 Markdown: {md_candidate}")
                    return md_candidate
                elif os.path.exists(sensitive_candidate):
                    logger.info(f"轉寫成功（敏感審核回退），生成: {sensitive_candidate}")
                    return sensitive_candidate
                else:
                    logger.warning("轉寫腳本退出碼為 0 但未找到輸出的 .md 檔案")
                    return None
            else:
                err = (result.stderr or result.stdout or f"code {result.returncode}").strip()[:300]
                logger.error(f"轉寫執行失敗: {err}")
                return None
        except Exception as e:
            logger.error(f"轉寫執行異常: {e}")
            return None
        finally:
            if os.path.exists(placeholder):
                try:
                    os.remove(placeholder)
                except OSError:
                    pass

    @staticmethod
    def extract_summary_from_md(md_path: str) -> str:
        """從轉寫生成的 Markdown 中提取 <!--SUMMARY_START--> 摘要看點"""
        if not os.path.exists(md_path):
            return ""
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                content = f.read()
            match = re.search(r"<!--SUMMARY_START-->\s*(.*?)\s*<!--SUMMARY_END-->", content, re.DOTALL)
            if match:
                summary_raw = match.group(1).strip()
                clean_lines = [line.lstrip("> ").strip() for line in summary_raw.splitlines()]
                return "\n".join(clean_lines).strip()
        except Exception as e:
            logger.warning(f"讀取 Markdown 摘要失敗: {e}")
        return ""

    @staticmethod
    def send_dingtalk_notification(
        webhook: str,
        author: str,
        title: str,
        publish_time: str,
        mp3_name: str,
        summary: str = "",
        phone: str = "",
        is_atall: bool = False
    ) -> bool:
        """推送作品更新與 AI 轉寫看點至釘釘"""
        if not webhook:
            logger.warning("未配置釘釘 Webhook，跳過推送")
            return False

        if summary:
            msg_content = (
                f"【精彩看点】\n{summary}\n\n"
                f"--------------------\n"
                f"[抖音视频更新] 【{author}】\n"
                f"标题：{title}\n"
                f"发布时间：{publish_time}\n"
                f"音频：{mp3_name}\n"
                f"状态：已转写完成，待上传百度网盘"
            )
        else:
            msg_content = (
                f"[抖音视频更新] 【{author}】\n"
                f"标题：{title}\n"
                f"发布时间：{publish_time}\n"
                f"音频：{mp3_name}\n"
                f"状态：音频提取完成，待上传百度网盘"
            )

        logger.info(f"發送釘釘更新通知: 【{author}】{title[:30]}")
        res = msg_push.dingtalk(webhook, msg_content, phone, is_atall)
        success = len(res.get("success", [])) > 0
        if success:
            logger.info("釘釘推送成功")
        else:
            logger.error(f"釘釘推送失敗: {res.get('error')}")
        return success

    @staticmethod
    def trigger_baidu_upload(script_path: str = "upload_baidu.sh") -> bool:
        """呼叫現有 upload_baidu.sh 執行百度雲上傳與本地校驗清理"""
        if not os.path.exists(script_path):
            logger.warning(f"百度雲上傳腳本不存在: {script_path}，將由伺服器 cron 定時上傳")
            return False

        logger.info(f"開始觸發百度網盤上傳: bash {script_path}")
        try:
            ret = subprocess.run(["bash", script_path], capture_output=True, text=True, timeout=1800)
            if ret.returncode == 0:
                logger.info("百度網盤上傳腳本執行完畢")
                return True
            else:
                logger.warning(f"百度網盤上傳腳本異常: {ret.stderr[:200]}")
                return False
        except Exception as e:
            logger.error(f"呼叫百度網盤上傳失敗: {e}")
            return False


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


def load_urls_from_config(config_path: str) -> List[Dict[str, str]]:
    """從 video_URL_config.ini 讀取博主主頁列表"""
    results = []
    if not os.path.exists(config_path):
        return results

    try:
        with open(config_path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [p.strip() for p in line.split(",") if p.strip()]
                if not parts:
                    continue
                url = parts[0]
                name = ""
                for part in parts[1:]:
                    if part.startswith("博主:") or part.startswith("博主："):
                        name = part.split(":", 1)[-1].split("：", 1)[-1].strip()
                    elif part.startswith("主播:") or part.startswith("主播："):
                        name = part.split(":", 1)[-1].split("：", 1)[-1].strip()
                results.append({"url": url, "name": name})
    except Exception as e:
        logger.error(f"讀取博主配置檔失敗 {config_path}: {e}")

    return results


def process_blogger_videos(
    url: str,
    custom_name: str,
    args,
    monitor: DouyinVideoMonitor,
    config_settings: Dict[str, Any]
) -> int:
    """處理單個博主的新作品檢測與下載"""
    try:
        sec_user_id = monitor.parse_sec_user_id(url)
    except Exception as e:
        logger.error(f"解析博主網址失敗 ({url}): {e}")
        return 0

    display_label = custom_name or sec_user_id
    logger.info(f"開始檢查博主: 【{display_label}】 ({sec_user_id})")

    try:
        videos = monitor.fetch_user_videos(sec_user_id, count=args.count)
    except Exception as e:
        logger.error(f"獲取博主作品清單失敗 【{display_label}】: {e}")
        return 0

    recent_videos = monitor.filter_recent_videos(
        videos,
        since_days=args.days,
        include_top=not args.no_top,
        video_only=not args.all_types
    )
    logger.info(f"【{display_label}】作品總數 {len(videos)} 部，符合日期篩選者 {len(recent_videos)} 部")

    history = load_history(args.history_file)
    transcribe_script = args.transcribe_script or config_settings.get("transcribe_script", "")
    dingtalk_webhook = args.dingtalk_webhook or config_settings.get("dingtalk_webhook", "")
    dingtalk_phone = config_settings.get("dingtalk_phone", "")
    dingtalk_is_atall = config_settings.get("dingtalk_is_atall", False)

    downloaded_count = 0

    for idx, v in enumerate(recent_videos, 1):
        top_tag = "[置頂] " if v["is_top"] else ""
        type_tag = "[影片]" if v["is_video"] else "[圖文]"
        downloaded_tag = " (已下載過)" if v["aweme_id"] in history else ""
        print(f"[{idx}] {top_tag}{type_tag} {v['publish_time']} | ID: {v['aweme_id']}{downloaded_tag}")
        print(f"    標題: {v['title'][:60]}")
        print(f"    時長: {v['duration_sec']:.1f}s")
        print("-" * 70)

        if args.download and v["video_url"]:
            if v["aweme_id"] in history:
                continue

            if args.max_download > 0 and downloaded_count >= args.max_download:
                logger.info(f"達到本次下載數量上限 ({args.max_download})，停止下載")
                break

            author_raw = custom_name or v["author"]
            author_name = re.sub(r'[\\/*?:"<>|]', "_", author_raw).strip() or "抖音博主"
            safe_title = re.sub(r'[\\/*?:"<>|]', "_", v["title"][:30]).strip() or v["aweme_id"]
            date_prefix = v["publish_time"][:10].replace("-", "")
            base_name = f"{date_prefix}_{safe_title}_{v['aweme_id']}"

            # 保存路徑：downloads/作者名/xxx.mp4，無縫相容現有 upload_baidu.sh
            target_dir = os.path.join(args.outdir, author_name)
            mp4_file = os.path.join(target_dir, f"{base_name}.mp4")
            mp3_file = os.path.join(target_dir, f"{base_name}.mp3")

            # 1. 下載 MP4
            success = False
            if not os.path.exists(mp4_file):
                success = monitor.download_video(v["video_url"], mp4_file)
            else:
                logger.info(f"MP4 已存在: {mp4_file}")
                success = True

            if not success:
                continue

            # 2. 提取 MP3
            if args.extract_mp3 and not os.path.exists(mp3_file):
                monitor.extract_mp3_from_mp4(mp4_file, mp3_file)

            # 3. 轉寫 MP3 -> MD
            md_file = None
            summary_text = ""
            if args.transcribe and os.path.exists(mp3_file):
                if transcribe_script:
                    md_file = monitor.transcribe_mp3_to_md(mp3_file, transcribe_script)
                    if md_file:
                        summary_text = monitor.extract_summary_from_md(md_file)
                else:
                    logger.info("未配置轉寫腳本路徑，跳過本機轉寫 (若在伺服器上會由後台處理)")

            # 4. 釘釘推送
            if args.notify_dingtalk and dingtalk_webhook:
                monitor.send_dingtalk_notification(
                    webhook=dingtalk_webhook,
                    author=author_name,
                    title=v["title"],
                    publish_time=v["publish_time"],
                    mp3_name=os.path.basename(mp3_file),
                    summary=summary_text,
                    phone=dingtalk_phone,
                    is_atall=dingtalk_is_atall
                )

            # 5. 寫入去重歷史
            history.add(v["aweme_id"])
            save_history(args.history_file, history)
            downloaded_count += 1

    return downloaded_count


def process_monitor_once(args, monitor: DouyinVideoMonitor, config_settings: Dict[str, Any]):
    """執行一次輪詢（支援單一 URL 或從 video_URL_config.ini 批量讀取）"""
    targets = []
    if args.url:
        targets.append({"url": args.url, "name": ""})
    else:
        cfg_path = args.url_config or os.path.join(_CURRENT_DIR, "config", "video_URL_config.ini")
        targets = load_urls_from_config(cfg_path)
        if not targets:
            logger.error(f"未指定 --url 且未在 {cfg_path} 找到有效的博主配置")
            return

    total_downloaded = 0
    for t in targets:
        count = process_blogger_videos(t["url"], t["name"], args, monitor, config_settings)
        total_downloaded += count

    # 觸發百度網盤上傳 (若有新作品下載)
    if args.upload_baidu and total_downloaded > 0:
        monitor.trigger_baidu_upload()


def main():
    parser = argparse.ArgumentParser(description="抖音博主新作品監控、下載、轉寫與推送流水線")
    parser.add_argument("--url", type=str, default="", help="單個博主個人主頁 URL 或 sec_user_id")
    parser.add_argument("--url-config", type=str, default="config/video_URL_config.ini", help="博主列表設定檔路徑")
    parser.add_argument("--days", type=float, default=7.0, help="篩選過去幾天內的作品 (預設 7 天)")
    parser.add_argument("--count", type=int, default=15, help="查詢最新作品數量 (預設 15 部)")
    parser.add_argument("--all-types", action="store_true", help="包含圖文作品 (預設僅下載純影片)")
    parser.add_argument("--no-top", action="store_true", help="排除置頂作品")
    parser.add_argument("--download", action="store_true", default=True, help="自動下載篩選出的視頻")
    parser.add_argument("--extract-mp3", action="store_true", default=True, help="下載後提取 MP3 音頻")
    parser.add_argument("--transcribe", action="store_true", default=True, help="提取音頻後呼叫腳本轉寫為 MD")
    parser.add_argument("--notify-dingtalk", action="store_true", default=True, help="轉寫完成後推送釘釘看點通知")
    parser.add_argument("--upload-baidu", action="store_true", help="主動觸發 upload_baidu.sh 上傳")
    parser.add_argument("--max-download", type=int, default=0, help="限制本次最多下載部數 (0 表示無限制)")
    parser.add_argument("--outdir", type=str, default="./downloads", help="下載保存目錄")
    parser.add_argument("--history-file", type=str, default="./downloads/download_history.json", help="去重歷史紀錄檔案")
    parser.add_argument("--transcribe-script", type=str, default="", help="指定轉寫腳本路徑")
    parser.add_argument("--dingtalk-webhook", type=str, default="", help="指定釘釘推送 Webhook")
    parser.add_argument("--daemon", action="store_true", help="啟用常駐模式 (每隔指定時長循環檢查)")
    parser.add_argument("--interval-hours", type=float, default=12.0, help="常駐模式檢查間隔小時數 (預設 12 小時)")

    args = parser.parse_args()

    config_settings = load_project_config()
    monitor = DouyinVideoMonitor()

    if args.daemon:
        interval_secs = int(args.interval_hours * 3600)
        logger.info(f"啟動常駐輪詢模式，每 {args.interval_hours} 小時 ({interval_secs} 秒) 檢查一次...")
        while True:
            try:
                process_monitor_once(args, monitor, config_settings)
            except Exception as e:
                logger.error(f"本次輪詢發生異常: {e}")
            logger.info(f"等待下次檢查 ({args.interval_hours} 小時後)...")
            time.sleep(interval_secs)
    else:
        process_monitor_once(args, monitor, config_settings)


if __name__ == "__main__":
    main()
