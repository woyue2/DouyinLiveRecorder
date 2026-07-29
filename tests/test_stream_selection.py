import importlib.util
import sys
import unittest
from pathlib import Path


def load_stream_selection():
    module_path = Path(__file__).parents[1] / "src" / "stream_selection.py"
    spec = importlib.util.spec_from_file_location("stream_selection", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


selection = load_stream_selection()


class StreamSelectionTests(unittest.TestCase):
    def setUp(self):
        self.stream_info = {
            "flv_url": "http://pull.example/stage/live.flv?sign=secret",
            "m3u8_url": "http://pull.example/stage/live.m3u8?sign=secret",
            "record_url": "http://pull.example/stage/live.m3u8?sign=secret",
        }

    def test_video_container_can_prefer_hls(self):
        source = selection.select_source_url(
            "https://live.douyin.com/123",
            self.stream_info,
            prefer_hls=True,
        )
        self.assertEqual(source, self.stream_info["m3u8_url"])

    def test_flv_behavior_is_preserved_without_hls_preference(self):
        source = selection.select_source_url(
            "https://live.douyin.com/123",
            self.stream_info,
        )
        self.assertEqual(source, self.stream_info["flv_url"])

    def test_h265_flv_falls_back_to_hls(self):
        self.stream_info["flv_url"] += "&codec=h265"
        source = selection.select_source_url(
            "https://live.douyin.com/123",
            self.stream_info,
        )
        self.assertEqual(source, self.stream_info["m3u8_url"])

    def test_missing_hls_keeps_available_source(self):
        self.stream_info["m3u8_url"] = None
        self.stream_info["record_url"] = self.stream_info["flv_url"]
        source = selection.select_source_url(
            "https://live.douyin.com/123",
            self.stream_info,
            prefer_hls=True,
        )
        self.assertEqual(source, self.stream_info["flv_url"])


if __name__ == "__main__":
    unittest.main()
