import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


def load_output_pipeline():
    module_path = Path(__file__).parents[1] / "src" / "output_pipeline.py"
    spec = importlib.util.spec_from_file_location("ffmpeg_output_pipeline", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


output_pipeline = load_output_pipeline()


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class FFmpegOutputIntegrationTests(unittest.TestCase):
    def generate_segments(self, output_pattern: Path, audio_format: str) -> list[Path]:
        if audio_format == "MP3":
            codec_args = ["-c:a", "libmp3lame", "-b:a", "96k"]
            segment_args = ["-segment_format", "mp3"]
        else:
            codec_args = ["-c:a", "aac", "-b:a", "96k"]
            segment_args = [
                "-segment_format",
                "mp4",
                "-segment_format_options",
                "movflags=+frag_keyframe+empty_moov",
            ]

        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=1000:sample_rate=44100",
                "-t",
                "2.2",
                "-map",
                "0:a",
                *codec_args,
                "-f",
                "segment",
                "-segment_time",
                "1",
                *segment_args,
                "-reset_timestamps",
                "1",
                str(output_pattern),
            ],
            check=True,
        )
        return output_pipeline.get_working_output_files(str(output_pattern))

    def probe(self, media_file: Path) -> dict:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=format_name,bit_rate,duration,size",
                "-show_entries",
                "stream=codec_name,codec_type",
                "-of",
                "json",
                str(media_file),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    def assert_format(self, audio_format: str, expected_codec: str, expected_container: str):
        with tempfile.TemporaryDirectory() as temp_dir:
            extension = audio_format.lower()
            working_pattern = Path(temp_dir) / f"record_%03d.{extension}.part"
            working_files = self.generate_segments(working_pattern, audio_format)

            self.assertGreaterEqual(len(working_files), 2)
            self.assertTrue(all(path.suffix == ".part" for path in working_files))
            self.assertFalse(list(Path(temp_dir).glob(f"*.{extension}")))

            published = output_pipeline.publish_output_files(str(working_pattern))

            self.assertEqual(len(published), len(working_files))
            self.assertFalse(list(Path(temp_dir).glob("*.part")))
            total_size = 0
            total_duration = 0.0
            for published_file in map(Path, published):
                probe_data = self.probe(published_file)
                audio_streams = [
                    stream
                    for stream in probe_data["streams"]
                    if stream["codec_type"] == "audio"
                ]
                codecs = {stream["codec_name"] for stream in audio_streams}
                self.assertIn(expected_codec, codecs)
                self.assertIn(expected_container, probe_data["format"]["format_name"])
                total_size += int(probe_data["format"]["size"])
                total_duration += float(probe_data["format"]["duration"])

            average_bitrate = total_size * 8 / total_duration
            self.assertTrue(
                80_000 <= average_bitrate <= 125_000,
                f"unexpected aggregate bitrate: {average_bitrate}",
            )

    def test_segmented_mp3_part_publish_and_container(self):
        self.assert_format("MP3", expected_codec="mp3", expected_container="mp3")

    def test_segmented_m4a_part_publish_and_container(self):
        self.assert_format("M4A", expected_codec="aac", expected_container="mp4")

    def test_direct_flv_stream_can_be_segmented_through_stdin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_path = Path(temp_dir) / "source.flv"
            working_pattern = Path(temp_dir) / "record_%03d.flv.part"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=160x90:rate=25",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=1000:sample_rate=44100",
                    "-t",
                    "2.2",
                    "-c:v",
                    "flv1",
                    "-g",
                    "25",
                    "-c:a",
                    "aac",
                    "-f",
                    "flv",
                    str(source_path),
                ],
                check=True,
            )

            process = subprocess.Popen(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "flv",
                    "-i",
                    "pipe:0",
                    "-map",
                    "0",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "copy",
                    "-f",
                    "segment",
                    "-segment_time",
                    "1",
                    "-segment_format",
                    "flv",
                    "-reset_timestamps",
                    "1",
                    str(working_pattern),
                ],
                stdin=subprocess.PIPE,
            )
            process.communicate(input=source_path.read_bytes())

            self.assertEqual(process.returncode, 0)
            working_files = output_pipeline.get_working_output_files(str(working_pattern))
            self.assertGreaterEqual(len(working_files), 2)

            published = output_pipeline.publish_output_files(str(working_pattern))
            self.assertEqual(len(published), len(working_files))
            for published_file in map(Path, published):
                self.assertIn("flv", self.probe(published_file)["format"]["format_name"])


if __name__ == "__main__":
    unittest.main()
