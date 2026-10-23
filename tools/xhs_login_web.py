# -*- encoding: utf-8 -*-
"""小紅書掃碼登入網頁服務。

用途：cookie 過期時，從通知點進來的真連結就是這個服務的頁面——
開啟即時產生一張新的二維碼，手機會員掃完確認，cookie 立刻寫回
config/local_secrets.ini 與 config.ini，不必再手動搬檔。

    GET /xhs-login?t=<token>            開啟即建新會話，回傳含 QR 的 HTML
    GET /xhs-login/status?t=&sid=       輪詢狀態（JSON）

僅綁 127.0.0.1，由 nginx 反向代理對外，避免直接暴露。
token 讀 config/xhs_login_token.txt，不存在則自動產生。
"""
import base64
import io
import json
import os
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import qrcode

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_TOOLS_DIR)
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

from login_xhs_server import (  # noqa: E402
    create_login_session,
    poll_login_status,
    update_config_cookie,
)

TOKEN_FILE = os.path.join(_ROOT, "config", "xhs_login_token.txt")
SESSION_TTL = 300          # 單一二維碼壽命上限（秒）
POLL_INTERVAL_HINT = 2000  # 前端輪詢間隔（毫秒）

_sessions: dict = {}
_lock = threading.Lock()


def load_token() -> str:
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            tok = f.read().strip()
            if tok:
                return tok
    except OSError:
        pass
    tok = secrets.token_urlsafe(24)
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(tok)
    return tok


TOKEN = load_token()


def clear_cooldown() -> None:
    """登入成功後清掉風控熔斷記錄。

    不清的話，監控雖然拿到新 cookie，仍會照舊睡滿 24 小時才再跑一次。
    """
    path = os.path.join(_ROOT, "downloads", "xhs_cooldown.json")
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as e:
        print(f"清除熔斷記錄失敗 {path}: {e}")


def _sweep() -> None:
    now = time.time()
    with _lock:
        for sid in [k for k, v in _sessions.items() if now - v["created"] > SESSION_TTL * 2]:
            _sessions.pop(sid, None)


def new_session(reuse_window: float = 90.0) -> tuple:
    """建一個新的掃碼會話；短時間內重開頁面時重用上一張還沒被掃的碼。

    7 分鐘連建 3 張碼就被小紅書 471 過（風控對高頻 create 敏感），
    所以重新整理不換新碼，除非上一張已超過 reuse_window 或已被掃過。
    """
    now = time.time()
    with _lock:
        for sess in _sessions.values():
            if (now - sess["created"] < reuse_window
                    and sess["state"] == "waiting"):
                sess_qr = sess.get("qr_url")
                if sess_qr:
                    return sess["sid"], sess_qr

    client, signer, qr_id, code, qr_url = create_login_session()
    sid = secrets.token_urlsafe(12)
    with _lock:
        _sessions[sid] = {
            "sid": sid, "client": client, "signer": signer, "qr_id": qr_id,
            "code": code, "qr_url": qr_url, "created": now,
            "state": "waiting", "info": None,
        }
    return sid, qr_url


def qr_data_uri(qr_url: str) -> str:
    img = qrcode.make(qr_url)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


PAGE = """<!doctype html>
<html lang="zh-Hant"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>小紅書掃碼登入</title>
<style>
  body {{ margin:0; padding:24px 16px; font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
         background:#f6f7f9; color:#1f2329; text-align:center; }}
  h1 {{ font-size:20px; margin:8px 0 4px; }}
  .sub {{ color:#6b7280; font-size:13px; margin-bottom:20px; }}
  .card {{ background:#fff; border-radius:14px; padding:20px; max-width:360px; margin:0 auto;
           box-shadow:0 1px 3px rgba(0,0,0,.08); }}
  img {{ width:100%; max-width:280px; height:auto; display:block; margin:0 auto; }}
  #state {{ margin-top:14px; font-size:15px; min-height:22px; }}
  .ok {{ color:#059669; font-weight:600; }}
  .bad {{ color:#dc2626; font-weight:600; }}
  .hint {{ color:#6b7280; font-size:12px; margin-top:14px; line-height:1.6; }}
</style></head>
<body>
<h1>小紅書掃碼登入</h1>
<div class="sub">伺服器端登入，掃完自動寫回 cookie</div>
<div class="card">
  <img id="qr" src="{qr}" alt="二維碼">
  <div id="state">等待掃描…</div>
  <div class="hint">用手機小紅書掃描上方二維碼，並在手機上點「確認登入」。<br>
  二維碼約 3 分鐘失效，過期後重新整理本頁即可。</div>
</div>
<script>
const TOKEN = {token_json}, SID = {sid_json};
const el = document.getElementById('state');
async function tick() {{
  try {{
    const r = await fetch('/xhs-login/status?t=' + encodeURIComponent(TOKEN) +
                          '&sid=' + encodeURIComponent(SID));
    const d = await r.json();
    if (d.state === 'scanned') {{ el.className=''; el.textContent='已掃描，請在手機上按「確認登入」…'; }}
    else if (d.state === 'ok') {{
      el.className='ok';
      el.textContent = '登入成功' + (d.nickname ? '：' + d.nickname : '') + '，cookie 已更新';
      return;                       // 停止輪詢
    }}
    else if (d.state === 'expired' || d.state === 'error') {{
      el.className='bad';
      el.textContent = (d.state === 'expired' ? '二維碼已過期' : ('出錯：' + (d.msg || ''))) + '，重新整理本頁可產生新碼';
      return;
    }}
  }} catch (e) {{}}
  setTimeout(tick, {interval});
}}
setTimeout(tick, {interval});
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 靜音，避免洗版
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict):
        self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def do_GET(self):
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if qs.get("t", [""])[0] != TOKEN:
            self._send(403, b"forbidden", "text/plain; charset=utf-8")
            return

        if parsed.path == "/xhs-login":
            self._serve_page()
        elif parsed.path == "/xhs-login/status":
            self._serve_status(qs.get("sid", [""])[0])
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def _serve_page(self):
        _sweep()
        try:
            sid, qr_url = new_session()
        except Exception as e:
            self._send(502, f"產生二維碼失敗: {e}".encode("utf-8"),
                       "text/plain; charset=utf-8")
            return
        html = PAGE.format(
            qr=qr_data_uri(qr_url),
            token_json=json.dumps(TOKEN),
            sid_json=json.dumps(sid),
            interval=POLL_INTERVAL_HINT,
        )
        self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")

    def _serve_status(self, sid: str):
        with _lock:
            sess = _sessions.get(sid)
        if not sess:
            self._json(200, {"state": "expired"})
            return
        if sess["state"] in ("ok", "expired", "error"):
            self._json(200, {"state": sess["state"], "nickname": (sess["info"] or {}).get("nickname")})
            return

        try:
            state, info = poll_login_status(
                sess["client"], sess["signer"], sess["qr_id"], sess["code"])
        except Exception as e:
            self._json(200, {"state": "error", "msg": str(e)[:120]})
            return

        if state == "ok":
            try:
                update_config_cookie(dict(sess["client"].cookies))
            except Exception as e:
                with _lock:
                    sess["state"] = "error"
                self._json(200, {"state": "error", "msg": f"寫入 cookie 失敗: {e}"})
                return
            clear_cooldown()

        with _lock:
            sess["state"] = state
            sess["info"] = info
        self._json(200, {
            "state": state,
            "nickname": (info or {}).get("nickname") if info else None,
        })


def main():
    host = os.environ.get("XHS_LOGIN_HOST", "127.0.0.1")
    port = int(os.environ.get("XHS_LOGIN_PORT", "8099"))
    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"xhs login web listening on http://{host}:{port}/xhs-login?t={TOKEN}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
