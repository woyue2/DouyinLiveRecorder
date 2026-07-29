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


if __name__ == "__main__":
    unittest.main()
