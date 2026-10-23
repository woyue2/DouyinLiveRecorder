# -*- encoding: utf-8 -*-
"""
小紅書雲端伺服器 (101.35.47.249) 純 API 輕量掃碼登入工具
優勢：
1. 運行在騰訊雲大陸原生 IP 上，徹底避免海外/代理導致的「300012 IP存在風險」
2. 零瀏覽器內存開銷（純 HTTP 請求，耗費內存 < 10MB）
3. 終端機直接打印 ASCII 黑白二維碼，隨掃即登
4. 掃碼授權成功後，自動將會話寫入 config/local_secrets.ini、config.ini [Cookie]
   與 config/xhs_auth.json

`create_login_session` / `poll_login_status` 也供 tools/xhs_login_web.py 的
網頁版登入使用。
"""

import os
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
_SECRETS_INI = os.path.join(_CONFIG_DIR, "local_secrets.ini")
_AUTH_JSON = os.path.join(_CONFIG_DIR, "xhs_auth.json")

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

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


def create_login_session():
    """建立一個新的掃碼登入會話。

    回傳 (client, signer, qr_id, code, qr_url)；失敗丟 RuntimeError。
    """
    signer = Xhshow()
    a1 = signer.generate_a1()
    web_id = signer.generate_web_id(a1)
    session_cookies = {"a1": a1, "webId": web_id}

    client = httpx.Client(
        headers={
            "User-Agent": _UA,
            "Origin": "https://www.xiaohongshu.com",
            "Referer": "https://www.xiaohongshu.com/",
        },
        cookies=session_cookies,
        timeout=15.0,
    )

    uri_create = "/api/sns/web/v1/login/qrcode/create"
    headers_create = signer.sign_headers_post(
        uri=uri_create, cookies=session_cookies, payload={})
    resp_create = client.post(
        f"https://edith.xiaohongshu.com{uri_create}", json={}, headers=headers_create)

    try:
        res_data = resp_create.json()
    except Exception as e:
        raise RuntimeError(f"二維碼接口回應無法解析: {e}")

    if not res_data.get("success"):
        raise RuntimeError(f"生成二維碼失敗: {res_data.get('msg')}")

    qr_info = res_data["data"]
    return client, signer, qr_info["qr_id"], qr_info["code"], qr_info["url"]


def poll_login_status(client, signer, qr_id: str, code: str):
    """查一次掃碼狀態。回傳 (state, info)。

    state ∈ waiting（未掃）/ scanned（已掃待確認）/ ok（已確認）/ expired / error。
    ok 時 info 為 login_info（含 user_id、nickname 等）。
    """
    uri_status = "/api/sns/web/v1/login/qrcode/status"
    params = {"qr_id": qr_id, "code": code}
    current_cookies = dict(client.cookies)
    headers_status = signer.sign_headers_get(
        uri=uri_status, cookies=current_cookies, params=params)
    r_status = client.get(
        f"https://edith.xiaohongshu.com{uri_status}", params=params, headers=headers_status)

    status_data = r_status.json()
    if not status_data.get("success"):
        return "error", status_data.get("msg")

    payload = status_data.get("data") or {}
    code_status = payload.get("code_status", 0)
    if code_status == 1:
        return "scanned", None
    if code_status == 2:
        return "ok", payload.get("login_info") or {}
    if code_status == 3:
        return "expired", None
    return "waiting", None


def qr_png_base64(qr_url: str) -> str:
    """把 QR 內容畫成 PNG，回傳 base64（給網頁內嵌用）。"""
    import base64
    import io
    img = qrcode.make(qr_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def run_login(timeout_seconds: int = 180) -> bool:
    logger.info("正在請求小紅書官方二維碼生成接口...")
    try:
        client, signer, qr_id, code, qr_url = create_login_session()
    except Exception as e:
        logger.error(str(e))
        return False

    print_ascii_qr(qr_url)
    logger.info(f"等待手機端掃碼確認 (最長等待 {timeout_seconds} 秒)...")

    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        try:
            state, info = poll_login_status(client, signer, qr_id, code)
            if state == "scanned":
                logger.info("📱 手機已掃描，請在手機小紅書上點擊【確認登入】...")
            elif state == "ok":
                logger.info("🎉 掃碼登入成功！")
                if info:
                    logger.info(f"登入用戶 ID: {info.get('user_id')}, 暱稱: {info.get('nickname')}")
                update_config_cookie(dict(client.cookies))
                return True
            elif state == "expired":
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
