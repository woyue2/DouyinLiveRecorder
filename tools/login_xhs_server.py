# -*- encoding: utf-8 -*-
"""小紅書登入 cookie 寫入工具。

純 API 掃碼（qrcode create/status）已在 2026-10-24 移除：狀態輪詢會被
小紅書 471 風控攔截要求滑塊驗證，純 API 無法處理。現行登入方式是
tools/xhs_browser_session.sh 開真實瀏覽器 + noVNC 投射，登入後由
tools/xhs_harvest_cookies.py 呼叫本檔的 update_config_cookie 寫回。
"""

import os
import json
import logging
import configparser

_CURRENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_DIR = os.path.join(_CURRENT_DIR, "config")
_CONFIG_INI = os.path.join(_CONFIG_DIR, "config.ini")
_SECRETS_INI = os.path.join(_CONFIG_DIR, "local_secrets.ini")
_AUTH_JSON = os.path.join(_CONFIG_DIR, "xhs_auth.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("XhsServerLogin")


def update_config_cookie(cookie_dict: dict):
    """把登入成功後的 Cookie 寫進所有需要的地方。

    local_secrets.ini 一定要寫：config.ini 會被 git 覆蓋，而監控優先讀
    local_secrets.ini 的 [Cookie] 小红书cookie。
    """
    os.makedirs(_CONFIG_DIR, exist_ok=True)

    cookie_parts = [f"{k}={v}" for k, v in cookie_dict.items()]
    cookie_str = "; ".join(cookie_parts)

    for ini_path, label in ((_CONFIG_INI, "config.ini"),
                            (_SECRETS_INI, "local_secrets.ini")):
        if not os.path.exists(ini_path):
            continue
        try:
            config = configparser.RawConfigParser()
            config.read(ini_path, encoding="utf-8-sig")
            if not config.has_section("Cookie"):
                config.add_section("Cookie")
            config.set("Cookie", "小红书cookie", cookie_str)
            with open(ini_path, "w", encoding="utf-8") as f:
                config.write(f)
            logger.info(f"已成功寫入 {label} [Cookie]")
        except Exception as e:
            logger.error(f"寫入 {label} 失敗: {e}")

    cookies_list = [{"name": k, "value": v, "domain": ".xiaohongshu.com"}
                    for k, v in cookie_dict.items()]
    with open(_AUTH_JSON, "w", encoding="utf-8") as f:
        json.dump({"cookies": cookies_list}, f, ensure_ascii=False, indent=2)
    logger.info(f"已成功保存會話至 {_AUTH_JSON}")
