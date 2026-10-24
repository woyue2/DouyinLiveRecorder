# -*- encoding: utf-8 -*-
"""從伺服器 Chromium（CDP 9222）收割抖音登入 cookie。

抖音對訪客大幅限制 aweme/post 的返回範圍（實測只給 18 部舊作品、詳情接口
直接 403），所以作品監控必須用登入身分。使用者透過 noVNC 在真瀏覽器登入
抖音後，本腳本把 cookie 寫回 config/local_secrets.ini 的 [Cookie] 抖音cookie。

    python3 tools/douyin_harvest_cookies.py --wait
"""
import argparse
import json
import sys
import time
from pathlib import Path

import websocket  # websocket-client

_TOOLS_DIR = Path(__file__).resolve().parent
_ROOT = _TOOLS_DIR.parent
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

from login_xhs_server import logger  # noqa: E402

CDP_HTTP = "http://127.0.0.1:9222"
CONFIG_INI = _ROOT / "config" / "config.ini"
SECRETS_INI = _ROOT / "config" / "local_secrets.ini"
REQUIRED = ("sessionid", "sessionid_ss")


def get_douyin_cookies() -> dict:
    import urllib.request
    with urllib.request.urlopen(f"{CDP_HTTP}/json/version", timeout=5) as r:
        ws_url = json.load(r)["webSocketDebuggerUrl"]
    ws = websocket.create_connection(ws_url, timeout=10)
    try:
        ws.send(json.dumps({"id": 1, "method": "Storage.getCookies"}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                cookies = msg["result"]["cookies"]
                break
    finally:
        ws.close()
    out = {}
    for c in cookies:
        if "douyin.com" in c.get("domain", ""):
            out[c["name"]] = c["value"]
    return out


def is_logged_in(cookies: dict) -> bool:
    """sessionid 只有登入後才下發；訪客只有 ttwid/UIFID_TEMP。"""
    return all(cookies.get(k) for k in REQUIRED)


def write_cookie(cookies: dict) -> None:
    import configparser
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
    for path, label in ((CONFIG_INI, "config.ini"), (SECRETS_INI, "local_secrets.ini")):
        if not path.exists():
            continue
        cp = configparser.RawConfigParser()
        cp.read(path, encoding="utf-8-sig")
        if not cp.has_section("Cookie"):
            cp.add_section("Cookie")
        cp.set("Cookie", "抖音cookie", cookie_str)
        with open(path, "w", encoding="utf-8") as f:
            cp.write(f)
        logger.info(f"已寫入 {label} [Cookie] 抖音cookie（{len(cookies)} 項）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", action="store_true")
    ap.add_argument("--interval", type=float, default=10.0)
    args = ap.parse_args()

    # 同小紅書：寫過就不重寫，但不退出——結束的話下次過期就沒人收割。
    last_written = ""
    while True:
        try:
            cookies = get_douyin_cookies()
            session = cookies.get("sessionid", "")
            if is_logged_in(cookies):
                if session != last_written:
                    write_cookie(cookies)
                    last_written = session
                    logger.info("🎉 已收割抖音登入 cookie（繼續待命）")
                if not args.wait:
                    return 0
            elif not args.wait:
                logger.info("瀏覽器裡還沒有抖音登入 session")
                return 1
        except Exception as e:
            if not args.wait:
                logger.error(f"CDP 連線失敗: {e}")
                return 1
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
