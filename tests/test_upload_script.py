import unittest
from pathlib import Path


class UploadScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = (Path(__file__).parents[1] / "upload_baidu.sh").read_text(
            encoding="utf-8"
        )

    def test_avoids_the_denied_slicing_path_for_current_files(self):
        self.assertIn('BYPY_SLICE_SIZE="1536M"', self.script)
        self.assertIn("NORMAL_UPLOAD_LIMIT_BYTES=1610612736", self.script)
        self.assertIn('if [ "$local_size" -ge "$NORMAL_UPLOAD_LIMIT_BYTES" ]', self.script)

    def test_uses_non_blocking_file_lock(self):
        self.assertIn('exec 9>"$LOCK_FILE"', self.script)
        self.assertIn("flock -n 9", self.script)

    def test_excludes_converted_from_source_scan(self):
        self.assertIn("-path ./converted -prune", self.script)
        self.assertIn("./converted/*) continue", self.script)

    def test_uploads_each_file_independently(self):
        self.assertIn("for ((index = 0; index < pending_count; index++))", self.script)
        self.assertIn('upload "$local_file" "$remote_file" overwrite', self.script)
        self.assertNotIn("syncup", self.script)

    def test_deletes_only_the_verified_file(self):
        self.assertIn('meta "$remote_file" \'$s\'', self.script)
        self.assertIn('[ "$remote_size" != "$local_size" ]', self.script)
        self.assertIn('rm -f -- "$local_file"', self.script)
        self.assertNotIn("rm -rf", self.script)

    def test_failure_is_retained_and_loop_continues(self):
        self.assertIn("failed_files+=(", self.script)
        self.assertIn("保留本地文件", self.script)
        upload_loop = self.script.split(
            "for ((index = 0; index < pending_count; index++))", 1
        )[1].split("done", 1)[0]
        self.assertNotIn("exit 1", upload_loop)

    def test_keeps_raw_success_and_failure_logs(self):
        self.assertIn('.success.log', self.script)
        self.assertIn('.failed.log', self.script)
        self.assertNotIn("-mtime", self.script)
        self.assertNotIn('rm -f -- "$success_log"', self.script)
        self.assertNotIn('rm -f -- "$failed_log"', self.script)

    def test_records_structured_sqlite_history(self):
        self.assertIn("upload_history.sqlite3", self.script)
        for command in ("run-start", "file-start", "file-finish", "run-finish"):
            self.assertIn(command, self.script)
        self.assertIn("ai-report", self.script)
        self.assertIn("ai_latest.json", self.script)
        self.assertIn("rapidupload_fallback", self.script)
        self.assertIn('--raw-output-file "$success_log"', self.script)
        self.assertIn('--raw-output-file "$failed_log"', self.script)

    def test_distinguishes_rapidupload_fallback_and_authorization_failure(self):
        self.assertIn("--type rapidupload_fallback", self.script)
        self.assertIn('error_category="authorization_31064"', self.script)
        authorization_check = self.script.index('[ "$error_code" = "31064" ]')
        process_check = self.script.index('[ "$process_exit_code" -ne 0 ]')
        self.assertLess(authorization_check, process_check)

    def test_sends_start_and_final_notifications(self):
        self.assertIn("开始逐文件上传", self.script)
        self.assertIn("上传完成", self.script)
        self.assertIn("上传异常", self.script)
        self.assertIn("local max_display_count=10", self.script)
        self.assertIn("待上传文件：${pending_summary}", self.script)
        self.assertIn("成功文件：${success_summary}", self.script)
        self.assertIn("失败文件：${failure_summary}", self.script)
        self.assertIn('success_files+=("$relative_file")', self.script)

    def test_notification_failure_does_not_fail_upload_task(self):
        function = self.script.split("notify_upload() {", 1)[1].split("\n}", 1)[0]
        self.assertIn('python3 "$SCRIPT_DIR/upload_notify.py"', function)
        self.assertIn("return 0", function)


if __name__ == "__main__":
    unittest.main()
