import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import msg_push


class DingTalkPushTests(unittest.TestCase):
    def test_failure_log_does_not_expose_webhook(self):
        webhook = "https://example.invalid/send?access_token=secret-token"
        output = io.StringIO()

        with patch.object(
            msg_push.opener,
            "open",
            side_effect=RuntimeError(f"request failed for {webhook}"),
        ):
            with redirect_stdout(output):
                result = msg_push.dingtalk(webhook, "测试通知")

        self.assertEqual(result, {"success": [], "error": [webhook]})
        self.assertIn("第1个推送地址", output.getvalue())
        self.assertIn("RuntimeError", output.getvalue())
        self.assertIn("request failed for", output.getvalue())
        self.assertIn("[已隐藏URL]", output.getvalue())
        self.assertNotIn(webhook, output.getvalue())
        self.assertNotIn("secret-token", output.getvalue())

    def test_error_details_are_shortened_and_token_values_are_hidden(self):
        error = RuntimeError(
            "access_token=very-secret-token " + "x" * 300
        )

        message = msg_push._safe_push_error(error)

        self.assertLessEqual(len(message), 214)
        self.assertIn("RuntimeError", message)
        self.assertIn("access_token=[已隐藏]", message)
        self.assertNotIn("very-secret-token", message)
        self.assertTrue(message.endswith("..."))


if __name__ == "__main__":
    unittest.main()
