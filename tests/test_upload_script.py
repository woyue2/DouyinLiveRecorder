import unittest
from pathlib import Path


class UploadScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = (
            Path(__file__).parents[1] / "upload_baidu.sh"
        ).read_text(encoding="utf-8")

    def test_keeps_requested_bypy_slice_size(self):
        self.assertIn("-s 500M", self.script)

    def test_uses_non_blocking_file_lock(self):
        self.assertIn('exec 9>"$LOCK_FILE"', self.script)
        self.assertIn("flock -n 9", self.script)

    def test_excludes_converted_from_source_scan(self):
        self.assertIn("-path ./converted -prune", self.script)
        self.assertIn("./converted/*)", self.script)

    def test_failed_upload_keeps_staged_files(self):
        failure_block = self.script.split("[上传失败]", maxsplit=1)[1]
        self.assertIn("[失败保留]", failure_block)
        self.assertNotIn('rm -rf -- "./converted"', failure_block)

    def test_logs_file_level_lifecycle(self):
        for marker in (
            "[发现文件]",
            "[移入待上传]",
            "[待上传文件]",
            "[上传成功]",
            "[失败保留]",
        ):
            self.assertIn(marker, self.script)

    def test_sends_upload_lifecycle_notifications(self):
        for message in (
            "[直播录制] 开始上传",
            "[直播录制] 上传成功",
            "[直播录制] 上传失败",
        ):
            self.assertIn(message, self.script)
        self.assertIn("文件：$pending_file_summary", self.script)

    def test_notification_lists_at_most_ten_pending_files(self):
        self.assertIn("pending_files=()", self.script)
        self.assertIn('pending_files+=("${pending_file#./converted/}")', self.script)
        self.assertIn("local max_display_count=10", self.script)
        self.assertIn("...另有 ", self.script)

    def test_notification_failure_does_not_fail_upload_task(self):
        notification_function = self.script.split(
            "notify_upload() {", maxsplit=1
        )[1].split("\n}", maxsplit=1)[0]
        self.assertIn('python3 "$SCRIPT_DIR/upload_notify.py"', notification_function)
        self.assertIn("return 0", notification_function)


if __name__ == "__main__":
    unittest.main()
