# -*- encoding: utf-8 -*-
"""從伺服器上的 Chromium（CDP 9222）收割小紅書登入 cookie。

使用者透過 noVNC 在真實頁面上完成登入（含滑塊驗證）後，web_session
cookie 會落在瀏覽器裡；本腳本輪詢 CDP，一發現就寫回：
  - config/local_secrets.ini 與 config.ini 的 [Cookie] 小红书cookie
  - config/xhs_auth.json
  - 清除 downloads/xhs_cooldown.json（熔斷記錄）

    python3 tools/xhs_harvest_cookies.py            # 立即檢查一次
    python3 tools/xhs_harvest_cookies.py --wait     # 持續等到登入成功
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

from login_xhs_server import update_config_cookie, logger  # noqa: E402

CDP_HTTP = "http://127.0.0.1:9222"
COOLDOWN_FILE = _ROOT / "downloads" / "xhs_cooldown.json"


def get_xhs_cookies() -> dict:
    """透過 CDP 取瀏覽器全部 cookie，回傳 xiaohongshu 網域的 {name: value}。"""
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
    return {c["name"]: c["value"] for c in cookies
            if "xiaohongshu.com" in c.get("domain", "")}


def has_session(cookies: dict) -> bool:
    return "web_session" in cookies and cookies["web_session"]


def cookie_works(cookies: dict) -> bool:
    """真的能用才算登入成功。

    訪客也會有 web_session 佔位（2026-10-24 踩過：收割到訪客 session 就
    寫回，實際打 API 全是 461 滑塊）。所以寫回前先打一次輕量請求驗證；
    461/滑塊或 -100 都視為未完成，繼續等使用者在瀏覽器裡過完驗證。
    """
    import urllib.parse
    try:
        from xhshow import Xhshow
    except ImportError:
        logger.error("xhshow 未安裝，無法驗證 cookie")
        return False

    signer = Xhshow()
    uri = "/api/sns/web/v1/user_posted"
    params = {"num": "1", "cursor": "", "user_id": "5ff0e6410000000001008400",
              "image_scenes": "FD_WM_WEBP"}
    query = urllib.parse.urlencode(params)
    cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
    try:
        headers = signer.sign_headers_get(uri=f"{uri}?{query}", cookies=cookies, params=params)
        import httpx
        r = httpx.get(
            f"https://edith.xiaohongshu.com{uri}?{query}",
            headers={**headers, "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                                               "Chrome/120.0.0.0 Safari/537.36")},
            cookies=cookies, timeout=15.0,
        )
    except Exception as e:
        logger.warning(f"驗證請求異常: {e}")
        return False
    if r.status_code == 200:
        try:
            data = r.json()
            if data.get("success"):
                return True
            logger.warning(f"驗證回應失敗: code={data.get('code')} msg={data.get('msg')}")
            return False
        except Exception:
            return False
    logger.warning(f"驗證請求被擋: HTTP {r.status_code}（等使用者過完滑塊再試）")
    return False


def write_cookie(cookies: dict) -> None:
    update_config_cookie(cookies)
    try:
        if COOLDOWN_FILE.exists():
            COOLDOWN_FILE.unlink()
            logger.info("已清除風控熔斷記錄，下一次巡檢直接恢復")
    except OSError as e:
        logger.warning(f"清除熔斷記錄失敗: {e}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", action="store_true", help="持續輪詢直到登入成功")
    ap.add_argument("--interval", type=float, default=5.0)
    args = ap.parse_args()

    last_check = 0.0
    while True:
        try:
            cookies = get_xhs_cookies()
            now = time.time()
            # 驗證要打小紅書 API，最多 60 秒一次（風控頻率紀律）
            if has_session(cookies) and now - last_check >= 60.0:
                last_check = now
                if cookie_works(cookies):
                    write_cookie(cookies)
                    logger.info("🎉 已收割小紅書登入 cookie（API 驗證通過）並寫回設定")
                    return 0
            if not args.wait:
                logger.info("瀏覽器裡還沒有可用的登入 session（訪客 session 不算）")
                return 1
        except Exception as e:
            if not args.wait:
                logger.error(f"CDP 連線失敗（瀏覽器沒在跑？）: {e}")
                return 1
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
