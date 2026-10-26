# -*- coding: utf-8 -*-
"""釘釘分群路由：三個分類各自設定，未設定則回退通用通道。"""
import configparser

import notify_route


def _cfg(values: dict) -> configparser.RawConfigParser:
    cp = configparser.RawConfigParser()
    cp.add_section("推送配置")
    for k, v in values.items():
        cp.set("推送配置", k, v)
    return cp


def test_each_category_uses_its_own_webhook():
    cp = _cfg({
        "钉钉推送接口链接": "default",
        "钉钉告警接口链接": "alert",
        "钉钉作品接口链接": "content",
        "钉钉录播接口链接": "pipeline",
    })
    routes = notify_route.load(cp)
    assert notify_route.pick(routes, "alert") == "alert"
    assert notify_route.pick(routes, "content") == "content"
    assert notify_route.pick(routes, "pipeline") == "pipeline"


def test_missing_category_falls_back_to_default():
    cp = _cfg({"钉钉推送接口链接": "default", "钉钉告警接口链接": "alert"})
    routes = notify_route.load(cp)
    assert notify_route.pick(routes, "content") == "default"
    assert notify_route.pick(routes, "pipeline") == "default"


def test_unknown_category_falls_back_to_default():
    cp = _cfg({"钉钉推送接口链接": "default"})
    assert notify_route.pick(notify_route.load(cp), "nope") == "default"


def test_all_empty_returns_empty_without_raising():
    routes = notify_route.load(_cfg({}))
    assert notify_route.pick(routes, "alert") == ""


def test_merge_config_files_later_overrides_earlier(tmp_path):
    a = tmp_path / "a.ini"
    b = tmp_path / "b.ini"
    a.write_text("[推送配置]\n钉钉推送接口链接 = old\n手机 = 111\n", encoding="utf-8")
    b.write_text("[推送配置]\n钉钉推送接口链接 = new\n", encoding="utf-8")
    cp = notify_route.merge_config_files(str(a), str(b))
    assert cp.get("推送配置", "钉钉推送接口链接") == "new"
    # 私密檔沒有的鍵仍要從通用檔讀到
    assert cp.get("推送配置", "手机") == "111"
