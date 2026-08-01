import configparser
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import upload_notify


class UploadNotifyTests(unittest.TestCase):
    def write_config(
        self,
        directory: str,
        webhook: str = "https://example.invalid/webhook-secret",
        at_all: str = "否",
    ) -> Path:
        config = configparser.RawConfigParser()
        config[upload_notify.PUSH_SECTION] = {
            "钉钉推送接口链接": webhook,
            "钉钉通知@对象(填手机号)": "",
            "钉钉通知@全体(是/否)": at_all,
        }
        config_file = Path(directory) / "config.ini"
        with config_file.open("w", encoding="utf-8") as file:
            config.write(file)
        return config_file

    def test_reuses_dingtalk_settings_from_config(self):
        with tempfile.TemporaryDirectory() as directory:
            config_file = self.write_config(directory, at_all="是")
            with patch.object(
                upload_notify,
                "dingtalk",
                return_value={"success": ["webhook"], "error": []},
            ) as mocked_dingtalk:
                succeeded = upload_notify.send_upload_notification(
                    "上传成功",
                    config_file,
                )

        self.assertTrue(succeeded)
        mocked_dingtalk.assert_called_once_with(
            "https://example.invalid/webhook-secret",
            "上传成功",
            "",
            True,
        )

    def test_reports_failure_without_webhook(self):
        with tempfile.TemporaryDirectory() as directory:
            config_file = self.write_config(directory, webhook="")
            with patch.object(upload_notify, "dingtalk") as mocked_dingtalk:
                succeeded = upload_notify.send_upload_notification(
                    "上传失败",
                    config_file,
                )

        self.assertFalse(succeeded)
        mocked_dingtalk.assert_not_called()


if __name__ == "__main__":
    unittest.main()
