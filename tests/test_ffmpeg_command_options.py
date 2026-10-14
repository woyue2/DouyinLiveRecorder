import ast
import unittest
from pathlib import Path


class FFmpegCommandOptionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        main_path = Path(__file__).parents[1] / "main.py"
        cls.tree = ast.parse(main_path.read_text(encoding="utf-8"))

    def get_recording_command_literals(self) -> list[str]:
        for node in ast.walk(self.tree):
            if not isinstance(node, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == "ffmpeg_command"
                for target in node.targets
            ):
                continue
            if not isinstance(node.value, ast.List):
                continue
            literals = [
                element.value
                for element in node.value.elts
                if isinstance(element, ast.Constant)
                and isinstance(element.value, str)
            ]
            if "-reconnect_streamed" in literals:
                return literals
        self.fail("recording FFmpeg command was not found")

    def test_reconnect_options_have_explicit_values_before_input(self):
        command = self.get_recording_command_literals()
        input_index = command.index("-i")
        # 注意：-reconnect_at_eof 已於 2026-08-08 移除。
        # 該選項會讓 ffmpeg 在輸入 EOF（直播真的結束）後仍持續重連，
        # 導致錄製進程無法乾淨結束、偵測不到下播，故不再使用。
        for option in (
            "-reconnect",
            "-reconnect_streamed",
        ):
            option_index = command.index(option)
            self.assertLess(option_index, input_index)
            self.assertEqual(command[option_index + 1], "1")

        self.assertNotIn("-reconnect_at_eof", command)
        self.assertLess(command.index("-reconnect_delay_max"), input_index)
        self.assertNotIn("-correct_ts_overflow", command)


if __name__ == "__main__":
    unittest.main()
