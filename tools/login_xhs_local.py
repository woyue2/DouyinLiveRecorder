# -*- encoding: utf-8 -*-
"""
小紅書本地可視化掃碼登入與憑證同步工具
功能：
1. 本地啟動有介面 Chrome (headless=False)，安全展示登入二維碼
2. 使用者使用小紅書【測試小號】掃碼登入
3. 登入成功後自動導出持久化會話 config/xhs_auth.json
4. 自動更新 config/config.ini 中的 [Cookie] 小红书cookie 欄位
5. 可選一鍵同步到雲端伺服器 (101.35.47.249)
"""

import os
import sys
import time
import json
import logging
import argparse
import subprocess
import configparser

from playwright.sync_api import sync_playwright

_CURRENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_DIR = os.path.join(_CURRENT_DIR, "config")
_CONFIG_INI = os.path.join(_CONFIG_DIR, "config.ini")
_AUTH_JSON = os.path.join(_CONFIG_DIR, "xhs_auth.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("XhsLogin")


def update_config_ini_cookie(cookie_str: str):
    """將提取出的小紅書 Cookie 寫入 config.ini"""
    if not os.path.exists(_CONFIG_INI):
        logger.warning(f"配置檔不存在: {_CONFIG_INI}")
        return

    config = configparser.RawConfigParser()
    config.read(_CONFIG_INI, encoding="utf-8-sig")

    if not config.has_section("Cookie"):
        config.add_section("Cookie")

    config.set("Cookie", "小红书cookie", cookie_str)
    with open(_CONFIG_INI, "w", encoding="utf-8") as f:
        config.write(f)
    logger.info("已成功將小紅書登入 Cookie 寫入 config/config.ini [Cookie]")


def run_local_login(timeout_seconds: int = 180) -> bool:
    """啟動本地可視化瀏覽器等待掃碼"""
    logger.info("正在啟動本地 Chrome 瀏覽器...")
    logger.info("⚠️ 風控提醒：請嚴格使用小紅書【測試小號】進行掃碼授權！")

    os.makedirs(_CONFIG_DIR, exist_ok=True)

    with sync_playwright() as p:
        # 啟動可視化瀏覽器視窗
        browser = p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized"
            ]
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800}
        )
        page = context.new_page()

        logger.info("正在打開小紅書首頁...")
        page.goto("https://www.xiaohongshu.com/explore", wait_until="domcontentloaded")

        print("\n" + "=" * 65)
        print("【請在彈出的瀏覽器視窗中，使用手機小紅書 APP 掃描登入二維碼】")
        print("（若未自動彈出登入框，請手動點擊右上角「登入」按鈕）")
        print(f"等待掃碼中（最長等待 {timeout_seconds} 秒）...")
        print("=" * 65 + "\n")

        start_time = time.time()
        logged_in = False

        while time.time() - start_time < timeout_seconds:
            # 判斷登入成功的真正標準：
            # 1. 前端狀態 window.__INITIAL_STATE__.user.loggedIn 變為 True
            try:
                is_logged = page.evaluate("""() => {
                    try {
                        if (window.__INITIAL_STATE__ && window.__INITIAL_STATE__.user && window.__INITIAL_STATE__.user.loggedIn === true) {
                            return true;
                        }
                    } catch (e) {}
                    return false;
                }""")
                if is_logged:
                    logger.info("🎉 檢測到小紅書登入成功 (loggedIn=true)！")
                    logged_in = True
                    break
            except Exception:
                pass

            # 2. 檢測頭像或用戶中心標識
            try:
                user_el = page.locator(".user-avatar, .side-bar .user, [class*='avatar']").first
                login_btn = page.locator(".login-btn, button:has-text('登录')").first
                if user_el.is_visible(timeout=500) and not login_btn.is_visible(timeout=500):
                    logger.info("🎉 檢測到用戶頭像已顯示，登入成功！")
                    logged_in = True
                    break
            except Exception:
                pass

            time.sleep(2.0)

        if not logged_in:
            logger.error("登入超時或未檢測到登入憑證，請稍後重試。")
            browser.close()
            return False

        # 給予 2 秒緩衝讓瀏覽器寫入全部 localStorage 與會話
        page.wait_for_timeout(2000)

        # 1. 保存 Playwright 會話狀態 (供 Playwright 復用)
        context.storage_state(path=_AUTH_JSON)
        logger.info(f"已保存 Playwright 登入狀態至: {_AUTH_JSON}")

        # 2. 導出 Cookie 字符串 (供 API / 請求庫復用)
        all_cookies = context.cookies()
        cookie_parts = [f"{c['name']}={c['value']}" for c in all_cookies]
        cookie_str = "; ".join(cookie_parts)

        update_config_ini_cookie(cookie_str)

        print("\n" + "=" * 65)
        print("✅ 小紅書【測試小號】登入成功！憑證已安全保存。")
        print("=" * 65 + "\n")

        browser.close()
        return True


def sync_to_server(pem_path: str = "C:/Users/Admin1/.ssh/t.pem", server_ip: str = "101.35.47.249"):
    """將生成的憑證一鍵同步至雲端伺服器"""
    if not os.path.exists(_AUTH_JSON):
        logger.error(f"本地會話檔案不存在: {_AUTH_JSON}")
        return False

    remote_dest = f"ubuntu@{server_ip}:/home/ubuntu/DouyinLiveRecorder-main/config/xhs_auth.json"
    logger.info(f"正在同步 {_AUTH_JSON} 到伺服器 {remote_dest}...")

    cmd = [
        "scp",
        "-i", pem_path,
        "-o", "StrictHostKeyChecking=no",
        _AUTH_JSON,
        remote_dest
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True)
        logger.info("✅ 憑證檔案成功同步至雲端伺服器！")
        return True
    except Exception as e:
        logger.warning(f"SCP 同步失敗: {e}，可稍後手動複製。")
        return False


def main():
    parser = argparse.ArgumentParser(description="小紅書本地可視化掃碼登入與憑證同步")
    parser.add_argument("--timeout", type=int, default=180, help="掃碼等待超時時間 (秒)")
    parser.add_argument("--sync", action="store_true", default=True, help="登入成功後是否自動同步至雲端伺服器")
    args = parser.parse_args()

    success = run_local_login(timeout_seconds=args.timeout)
    if success and args.sync:
        sync_to_server()


if __name__ == "__main__":
    main()
