import contextlib
import io
import json
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from support import SCRIPTS
import recon


class StreamEntryTest(unittest.TestCase):
    def test_stream_reaches_parent_before_child_finishes(self):
        with tempfile.TemporaryDirectory() as directory:
            child = Path(directory, "child.py")
            child.write_text(
                'import sys\nprint(\'{"event":"ready"}\', flush=True)\n'
                'sys.stdin.readline()\nprint(\'{"schema":"agent/1"}\')\n',
                encoding="utf-8")
            entry = (
                "import sys; sys.path.insert(0, sys.argv[1]); import recon; "
                "recon.TOOLS['agent']=('python', sys.argv[2], 'fixture'); "
                "sys.exit(recon.main(['agent', '--stream']))")
            with subprocess.Popen(
                    [sys.executable, "-c", entry, SCRIPTS, str(child)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True) as proc:
                try:
                    ready, _, _ = select.select([proc.stdout], [], [], 5)
                    self.assertTrue(ready, "stream was buffered until child exit")
                    self.assertEqual(json.loads(proc.stdout.readline()), {"event": "ready"})
                finally:
                    stdout, stderr = proc.communicate("continue\n", timeout=10)
                self.assertEqual(proc.returncode, 0, stderr)
                self.assertIn('"agent/1"', stdout)

    def test_direct_agent_rejects_json_and_stream(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(Path(SCRIPTS, "agent.py")),
                 "--out", directory, "--stream", "--json"],
                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertIn("--stream and --json are mutually exclusive", result.stderr)

    def test_json_and_stream_are_rejected_before_launch(self):
        with patch.object(recon, "run", return_value=(0, '{}', '')) as run, contextlib.redirect_stderr(io.StringIO()):
            rc = recon.main(["agent", "--stream", "--json"])
        self.assertEqual(rc, 2)
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
