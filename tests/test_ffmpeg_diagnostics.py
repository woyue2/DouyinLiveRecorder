import importlib.util
import io
import os
import sys
import unittest
from pathlib import Path


def load_ffmpeg_diagnostics():
    module_path = Path(__file__).parents[1] / "src" / "ffmpeg_diagnostics.py"
    spec = importlib.util.spec_from_file_location("ffmpeg_diagnostics", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


diagnostics = load_ffmpeg_diagnostics()


class FFmpegDiagnosticsTests(unittest.TestCase):
    def test_extracts_actual_input_url(self):
        command = ["ffmpeg", "-y", "-i", "https://example.com/live.flv?token=1", "-f", "mp4", "out.mp4"]
        self.assertEqual(
            diagnostics.get_ffmpeg_input_url(command),
            "https://example.com/live.flv?token=1",
        )

    def test_log_path_uses_one_file_for_segment_pattern(self):
        log_path = diagnostics.get_ffmpeg_log_path("/tmp/anchor_%03d.mp4")
        self.assertEqual(log_path, os.path.normpath("/tmp/anchor_segments.mp4.ffmpeg.log"))

    def test_header_preserves_command_and_source(self):
        output = io.StringIO()
        command = ["ffmpeg", "-i", "https://example.com/直播.flv", "out.mp4"]
        diagnostics.write_ffmpeg_log_header(output, "主播", command)
        text = output.getvalue()
        self.assertIn("input_url: https://example.com/直播.flv", text)
        self.assertIn('"https://example.com/直播.flv"', text)

    def test_negative_return_code_names_signal_on_posix(self):
        description = diagnostics.describe_return_code(-11)
        if os.name == "nt":
            self.assertEqual(description, "-11")
        else:
            self.assertEqual(description, "-11 (signal 11: SIGSEGV)")


if __name__ == "__main__":
    unittest.main()
