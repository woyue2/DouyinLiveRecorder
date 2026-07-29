from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def is_flv_preferred_platform(link: str) -> bool:
    return any(platform in link for platform in ("douyin", "tiktok"))


def select_source_url(
    link: str,
    stream_info: Mapping[str, Any],
    *,
    prefer_hls: bool = False,
) -> str | None:
    m3u8_url = stream_info.get("m3u8_url")
    if prefer_hls and m3u8_url:
        return m3u8_url

    if is_flv_preferred_platform(link):
        flv_url = stream_info.get("flv_url")
        codec = _get_query_param(flv_url, "codec")
        if codec == "h265":
            return m3u8_url or stream_info.get("record_url")
        if flv_url:
            return flv_url

    return stream_info.get("record_url")


def _get_query_param(url: str | None, name: str) -> str | None:
    if not url or "?" not in url:
        return None

    query = url.split("?", maxsplit=1)[1]
    for item in query.split("&"):
        key, separator, value = item.partition("=")
        if separator and key == name:
            return value
    return None
