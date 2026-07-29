import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


def load_module(name: str, file_name: str):
    module_path = Path(__file__).parents[1] / "src" / file_name
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


output_pipeline = load_module("output_pipeline", "output_pipeline.py")
recording_config = load_module("recording_config", "recording_config.py")
get_working_output_path = output_pipeline.get_working_output_path
get_working_output_files = output_pipeline.get_working_output_files
publish_output_files = output_pipeline.publish_output_files
publish_completed_segments = output_pipeline.publish_completed_segments
RecordFormat = recording_config.RecordFormat
RecordingConfig = recording_config.RecordingConfig
normalize_audio_bitrate = recording_config.normalize_audio_bitrate


class RecordFormatTests(unittest.TestCase):
    def test_audio_aliases_are_canonical(self):
        self.assertEqual(RecordFormat.parse("mp3音频").name, "MP3")
        self.assertEqual(RecordFormat.parse("MP3").name, "MP3")
        self.assertEqual(RecordFormat.parse("m4a音频").container, "mp4")

    def test_invalid_value_falls_back_to_default(self):
        self.assertEqual(RecordFormat.parse("unknown", "MP4").name, "MP4")

    def test_recording_config_fingerprint_tracks_format_changes(self):
        before = RecordingConfig("原画", "https://example.com/live", "anchor", "MP4", "line")
        after = RecordingConfig("原画", "https://example.com/live", "anchor", "MP3", "line")
        self.assertNotEqual(before.fingerprint, after.fingerprint)

    def test_audio_bitrate_is_normalized_and_bounded(self):
        self.assertEqual(normalize_audio_bitrate("96"), "96k")
        self.assertEqual(normalize_audio_bitrate("128k"), "128k")
        self.assertEqual(normalize_audio_bitrate("invalid"), "96k")
        self.assertEqual(normalize_audio_bitrate("500"), "96k")


class OutputPipelineTests(unittest.TestCase):
    def test_single_output_is_atomically_published(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            final_path = Path(temp_dir) / "record.mp3"
            working_path = Path(get_working_output_path(str(final_path)))
            working_path.write_bytes(b"audio")

            published = publish_output_files(str(working_path))

            self.assertEqual(published, [str(final_path)])
            self.assertEqual(final_path.read_bytes(), b"audio")
            self.assertFalse(working_path.exists())

    def test_segment_outputs_are_published_together(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pattern = Path(temp_dir) / "record_%03d.m4a.part"
            for index in range(2):
                Path(str(pattern).replace("%03d", f"{index:03d}")).write_bytes(b"audio")

            published = publish_output_files(str(pattern))

            self.assertEqual(len(published), 2)
            self.assertTrue((Path(temp_dir) / "record_000.m4a").exists())
            self.assertTrue((Path(temp_dir) / "record_001.m4a").exists())

    def test_only_closed_segments_are_published_while_recording(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            pattern = Path(temp_dir) / "record_%03d.mp3.part"
            for index in range(3):
                Path(str(pattern).replace("%03d", f"{index:03d}")).write_bytes(b"audio")

            published = publish_completed_segments(str(pattern))

            self.assertEqual(len(published), 2)
            self.assertTrue((Path(temp_dir) / "record_000.mp3").exists())
            self.assertTrue((Path(temp_dir) / "record_001.mp3").exists())
            self.assertTrue((Path(temp_dir) / "record_002.mp3.part").exists())


if __name__ == "__main__":
    unittest.main()
