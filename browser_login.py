# -*- encoding: utf-8 -*-
"""伺服器端登入入口（noVNC 投射的真瀏覽器）。

小紅書與抖音的 cookie 都靠使用者在這個頁面操作真瀏覽器取得，所以位址與
密碼只能有一份：先前分別硬編碼在 xhs_browser_session.sh、小紅書通知、
抖音通知三處，改一個漏兩個。

密碼要與 tools/xhs_browser_session.sh 的 VNC_PASSWORD 保持一致。
"""

BASE_URL = "http://101.35.47.249:8080"
VNC_PASSWORD = "xhs8888"


def login_url() -> str:
    return (f"{BASE_URL}/vnc/vnc.html?autoconnect=1"
            f"&resize=scale&password={VNC_PASSWORD}")
