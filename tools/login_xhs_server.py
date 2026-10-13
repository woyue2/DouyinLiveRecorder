# -*- encoding: utf-8 -*-
"""
小紅書雲端伺服器 (101.35.47.249) 純 API 輕量掃碼登入工具
優勢：
1. 運行在騰訊雲大陸原生 IP 上，徹底避免海外/代理導致的「300012 IP存在風險」
2. 零瀏覽器內存開銷（純 HTTP 請求，耗費內存 < 10MB）
3. 終端機直接打印 ASCII 黑白二維碼，隨掃即登
4. 掃碼授權成功後，自動將會話寫入 config/xhs_auth.json 與 config.ini [Cookie]
"""

import os
import sys
import time
import json
import logging
import argparse
import configparser

import httpx
import qrcode
from xhshow import Xhshow

_CURRENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_DIR = os.path.join(_CURRENT_DIR, "config")
_CONFIG_INI = os.path.join(_CONFIG_DIR, "config.ini")
_AUTH_JSON = os.path.join(_CONFIG_DIR, "xhs_auth.json")
_WEB_POSTS_DIR = os.path.join(_CURRENT_DIR, "web_posts")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("XhsServerLogin")


def print_ascii_qr(qr_url: str):
    """在終端機直接印出 ASCII 二維碼供手機掃描"""
    qr = qrcode.QRCode(border=1)
    qr.add_data(qr_url)
    qr.make(fit=True)
    print("\n" + "=" * 65)
    print("請使用手機小紅書【測試小號】APP 掃描下方二維碼：")
    print("=" * 65 + "\n")
    qr.print_ascii(invert=True)
    print("\n" + "=" * 65 + "\n")


def update_config_cookie(cookie_dict: dict):
    """將登入成功後的 Cookie 寫入 config.ini 與 xhs_auth.json"""
    os.makedirs(_CONFIG_DIR, exist_ok=True)

    cookie_parts = [f"{k}={v}" for k, v in cookie_dict.items()]
    cookie_str = "; ".join(cookie_parts)

    # 1. 更新 config.ini
    if os.path.exists(_CONFIG_INI):
        config = configparser.RawConfigParser()
        config.read(_CONFIG_INI, encoding="utf-8-sig")
        if not config.has_section("Cookie"):
            config.add_section("Cookie")
        config.set("Cookie", "小红书cookie", cookie_str)
        with open(_CONFIG_INI, "w", encoding="utf-8") as f:
            config.write(f)
        logger.info("已成功寫入 config/config.ini [Cookie]")

    # 2. 保存為 xhs_auth.json
    cookies_list = [{"name": k, "value": v, "domain": ".xiaohongshu.com"} for k, v in cookie_dict.items()]
    with open(_AUTH_JSON, "w", encoding="utf-8") as f:
        json.dump({"cookies": cookies_list}, f, ensure_ascii=False, indent=2)
    logger.info(f"已成功保存會話至 {_AUTH_JSON}")


def run_login(timeout_seconds: int = 180) -> bool:
    logger.info("正在請求小紅書官方二維碼生成接口...")
    signer = Xhshow()
    a1 = signer.generate_a1()
    web_id = signer.generate_web_id(a1)
    session_cookies = {"a1": a1, "webId": web_id}

    client = httpx.Client(
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Origin": "https://www.xiaohongshu.com",
            "Referer": "https://www.xiaohongshu.com/",
        },
        cookies=session_cookies,
        timeout=15.0
    )

    # 1. 生成二維碼
    uri_create = "/api/sns/web/v1/login/qrcode/create"
    headers_create = signer.sign_headers_post(uri=uri_create, cookies=session_cookies, payload={})
    resp_create = client.post(f"https://edith.xiaohongshu.com{uri_create}", json={}, headers=headers_create)

    res_data = resp_create.json()
    if not res_data.get("success"):
        logger.error(f"生成二維碼失敗: {res_data.get('msg')}")
        return False

    qr_info = res_data["data"]
    qr_id = qr_info["qr_id"]
    code = qr_info["code"]
    qr_url = qr_info["url"]

    # 2. 在終端打印 ASCII 二維碼
    print_ascii_qr(qr_url)

    # 同步保存圖片至 web_posts/ 目錄 (可選備用)
    try:
        os.makedirs(_WEB_POSTS_DIR, exist_ok=True)
        img_path = os.path.join(_WEB_POSTS_DIR, "login_qr.png")
        qr_img = qrcode.make(qr_url)
        qr_img.save(img_path)
        logger.info(f"二維碼圖片備用網址: http://101.35.47.249:8080/posts/login_qr.png")
    except Exception:
        pass

    logger.info(f"等待手機端掃碼確認 (最長等待 {timeout_seconds} 秒)...")
    uri_status = "/api/sns/web/v1/login/qrcode/status"
    params = {"qr_id": qr_id, "code": code}

    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        current_cookies = dict(client.cookies)
        headers_status = signer.sign_headers_get(uri=uri_status, cookies=current_cookies, params=params)

        try:
            r_status = client.get(f"https://edith.xiaohongshu.com{uri_status}", params=params, headers=headers_status)
            status_data = r_status.json()
            if status_data.get("success"):
                code_status = status_data.get("data", {}).get("code_status", 0)
                if code_status == 0:
                    pass  # 等待掃描
                elif code_status == 1:
                    logger.info("📱 手機已掃描，請在手機小紅書上點擊【確認登入】...")
                elif code_status == 2:
                    logger.info("🎉 掃碼登入成功！")
                    # 登入成功，提取會話 Cookies
                    final_cookies = dict(client.cookies)
                    login_data = status_data.get("data", {}).get("login_info", {})
                    if login_data:
                        logger.info(f"登入用戶 ID: {login_data.get('user_id')}, 暱稱: {login_data.get('nickname')}")

                    update_config_cookie(final_cookies)
                    # 清理臨時二維碼圖片
                    if os.path.exists(os.path.join(_WEB_POSTS_DIR, "login_qr.png")):
                        try:
                            os.remove(os.path.join(_WEB_POSTS_DIR, "login_qr.png"))
                        except OSError:
                            pass
                    return True
                elif code_status == 3:
                    logger.warning("二維碼已過期，請重新執行腳本。")
                    return False
        except Exception as e:
            logger.warning(f"輪詢狀態異常: {e}")

        time.sleep(2.0)

    logger.error("登入等待超時。")
    return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="小紅書伺服器輕量掃碼登入工具")
    parser.add_argument("--timeout", type=int, default=180, help="掃碼等待超時時間 (秒)")
    args = parser.parse_args()
    run_login(timeout_seconds=args.timeout)
