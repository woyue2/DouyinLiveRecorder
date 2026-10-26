#!/usr/bin/env python3
"""Send upload lifecycle notifications through the existing push module."""

import configparser
import sys
from pathlib import Path

import notify_route
from msg_push import dingtalk


PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = PROJECT_DIR / "config" / "config.ini"
SECRETS_FILE = PROJECT_DIR / "config" / "local_secrets.ini"
PUSH_SECTION = "推送配置"
TRUE_VALUES = {"是", "true", "1", "yes", "on"}


def send_upload_notification(content: str, config_file: Path = CONFIG_FILE) -> bool:
    # 密鑰在 local_secrets.ini、通用設定在 config.ini，兩份都要讀。
    # 只讀 config.ini 的話，分群鍵（寫在私密檔）永遠讀不到。
    config = notify_route.merge_config_files(str(config_file), str(SECRETS_FILE))
    if not config.has_section(PUSH_SECTION):
        print(f"无法读取推送配置文件: {config_file}")
        return False

    # 網盤上傳屬於背景流水，走「錄播流水線」群；未設定才回退通用通道
    webhook = notify_route.pick(notify_route.load(config), "pipeline")
    phone = config.get(
        PUSH_SECTION, "钉钉通知@对象(填手机号)", fallback=""
    ).strip()
    at_all_text = config.get(
        PUSH_SECTION, "钉钉通知@全体(是/否)", fallback="否"
    ).strip().lower()

    if not webhook:
        print("未配置钉钉推送接口链接")
        return False

    result = dingtalk(
        webhook,
        content,
        phone,
        at_all_text in TRUE_VALUES,
    )
    success_count = len(result["success"])
    error_count = len(result["error"])
    print(f"钉钉通知结果：成功={success_count}，失败={error_count}")
    return success_count > 0 and error_count == 0


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("用法: upload_notify.py <通知内容>")
        return 2

    return 0 if send_upload_notification(argv[1]) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
