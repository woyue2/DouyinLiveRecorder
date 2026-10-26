# -*- encoding: utf-8 -*-
"""釘釘分群路由（告警 / 作品 / 錄播）。

四個模組（main、小紅書、抖音、網盤上傳）都要判斷「這條訊息該去哪個群」，
先前各自寫一份讀鍵與回退邏輯，改一處漏三處。集中在這裡，回退規則只有一份：
單獨設定的群沒填，就退回通用通道 `钉钉推送接口链接`。
"""
import os

SECTION = "推送配置"
DEFAULT_KEY = "钉钉推送接口链接"
CATEGORY_KEYS = {
    "alert": "钉钉告警接口链接",
    "content": "钉钉作品接口链接",
    "pipeline": "钉钉录播接口链接",
}


def _get(config, key: str) -> str:
    if not config.has_section(SECTION):
        return ""
    return config.get(SECTION, key, fallback="").strip()


def load(config) -> dict:
    """從 configparser 讀四個鍵，回傳已套用回退的 {category: url}。

    另含 "default" 鍵，供需要明確知道通用通道的呼叫端使用。
    """
    default = _get(config, DEFAULT_KEY)
    routes = {cat: (_get(config, key) or default) for cat, key in CATEGORY_KEYS.items()}
    routes["default"] = default
    return routes


def pick(routes: dict, category: str) -> str:
    """取某分類的 Webhook；該分類為空時退回通用通道。"""
    return routes.get(category) or routes.get("default", "")


def merge_config_files(*paths: str) -> "object":
    """依序讀多個 ini（後者覆蓋前者），回傳 configparser 物件。

    local_secrets.ini 會覆蓋 config.ini——密鑰只在私密檔裡，但通用設定
    （手機號、@全體）仍在 config.ini，兩邊都要讀到。
    """
    import configparser
    cp = configparser.RawConfigParser()
    for path in paths:
        if path and os.path.exists(path):
            cp.read(path, encoding="utf-8-sig")
    return cp
