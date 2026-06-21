import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src")))

from jack.cli import main


class CliTest(unittest.TestCase):
    def test_parse_error_is_reported_without_traceback(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jack") as source:
            source.write("return 1;")
            source.flush()

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
                main(["-i", source.name])

        self.assertEqual(raised.exception.code, 1)
        self.assertIn("jack:", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
