import contextlib
import io
import json
import importlib.util
import os
import signal
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from support import SCRIPTS, serve, response, long_html
from test_process import PEER, Peers, running
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


@unittest.skipUnless(os.name == 'posix', 'POSIX process groups')
class CancellationEntryTest(unittest.TestCase):
    def assert_closed(self, conn, stream):
        ready, _, _ = select.select([conn], [], [], 4)
        self.assertTrue(ready, 'owned descendant survived CLI cancellation')
        self.assertEqual(stream.readline(), b'')

    def test_capture_and_stream_cancel_owned_tree_only(self):
        for stream_mode in (False, True):
            for signum in (signal.SIGINT, signal.SIGTERM):
                with self.subTest(stream=stream_mode, signal=signum), tempfile.TemporaryDirectory() as directory:
                    peers = Peers()
                    unrelated = Peers()
                    peer = Path(directory, 'peer.py')
                    peer.write_text(PEER, encoding='utf-8')
                    child = Path(directory, 'child.py')
                    child.write_text(
                        'import runpy, subprocess, sys\n'
                        f'sys.argv = [{str(peer)!r}, {str(peers.port)!r}, "normal"]\n'
                        'subprocess.Popen([sys.executable] + sys.argv)\n'
                        f'runpy.run_path({str(peer)!r}, run_name="__main__")\n',
                        encoding='utf-8')
                    entry = (
                        "import sys; sys.path.insert(0, sys.argv[1]); import recon; "
                        "recon.TOOLS['agent']=('python', sys.argv[2], 'fixture'); "
                        "sys.exit(recon.main(['agent'] + sys.argv[3:]))")
                    try:
                        with running([sys.executable, str(peer), str(unrelated.port), 'normal']) as other:
                            other_conn, other_stream, _ = unrelated.accept()
                            with running([sys.executable, '-c', entry, SCRIPTS, str(child)] +
                                         (['--stream'] if stream_mode else [])) as proc:
                                owned = [peers.accept(), peers.accept()]
                                proc.send_signal(signum)
                                for conn, stream, _ in owned:
                                    self.assert_closed(conn, stream)
                                _, stderr = proc.communicate(timeout=5)
                                self.assertEqual(proc.returncode, 128 + signum, stderr)
                                other_conn.sendall(b'p')
                                self.assertEqual(other_stream.readline(), b'PONG\n')
                                self.assertIsNone(other.poll())
                    finally:
                        peers.close()
                        unrelated.close()

    def test_real_agent_nested_crawl_adapter_cancellation(self):
        runtimes = ['stdlib']
        if importlib.util.find_spec('langgraph') is not None:
            runtimes.append('langgraph')
        for runtime in runtimes:
            for direct in (False, True):
                for stream_mode in (False, True):
                    for signum in (signal.SIGINT, signal.SIGTERM):
                        with self.subTest(runtime=runtime, direct=direct, stream=stream_mode,
                                          signal=signum), tempfile.TemporaryDirectory() as directory:
                            self.cancel_agent(directory, runtime, direct, stream_mode, signum)

    def test_symlink_cli_keeps_ignored_term_descendants_in_one_group(self):
        runtimes = ['stdlib']
        if importlib.util.find_spec('langgraph') is not None:
            runtimes.append('langgraph')
        for runtime in runtimes:
            for stream_mode in (False, True):
                for signum in (signal.SIGINT, signal.SIGTERM):
                    with self.subTest(runtime=runtime, stream=stream_mode, signal=signum), \
                            tempfile.TemporaryDirectory() as directory:
                        link = Path(directory, 'repo-link')
                        link.symlink_to(Path(SCRIPTS).parent, target_is_directory=True)
                        self.cancel_agent(directory, runtime, False, stream_mode, signum,
                                          scripts=link / 'scripts', peer_mode='ignore')

    def cancel_agent(self, directory, runtime, direct, stream_mode, signum,
                     scripts=SCRIPTS, peer_mode='normal'):
        peers = Peers()
        peer = Path(directory, 'peer.py')
        peer.write_text(PEER, encoding='utf-8')
        adapter = Path(directory, 'adapter.py')
        adapter.write_text(
            'import runpy, subprocess, sys\n'
            f'sys.argv = [{str(peer)!r}, {str(peers.port)!r}, {peer_mode!r}]\n'
            'subprocess.Popen([sys.executable] + sys.argv, stdout=subprocess.DEVNULL, '
            'stderr=subprocess.DEVNULL)\n'
            f'runpy.run_path({str(peer)!r}, run_name="__main__")\n', encoding='utf-8')
        config = Path(directory, 'arms.json')
        config.write_text(json.dumps({'arms': [{
            'name': 'browser:fixture', 'transport': 'cmd', 'capabilities': ['js'],
            'command': [sys.executable, str(adapter), '{url}']}]}), encoding='utf-8')
        out = Path(directory, 'out')
        command = ([sys.executable, str(Path(scripts, 'agent.py'))] if direct else
                   [sys.executable, str(Path(scripts, 'recon.py')), 'agent'])
        args = ['--out', str(out), '--runtime', runtime]
        if runtime == 'langgraph':
            args += ['--thread-id', 'cancel']
        hits = []
        def page(handler):
            hits.append(handler.path)
            response(handler, 200, long_html())
        try:
            with serve({'/': page}) as base:
                with running(command + args + [base + '/', '--arms', 'browser:fixture',
                             '--arms-file', str(config), '--i-have-permission', '--delay', '0',
                             '--timeout', '60', '--max-rounds', '1'] +
                             (['--stream'] if stream_mode else [])) as proc:
                    owned = [peers.accept(), peers.accept()]
                    # Track only fixture ancestors so failed regressions can clean them up.
                    cursor = owned[0][2]['pid']
                    ancestors = []
                    while cursor != proc.pid:
                        self.assertGreater(cursor, 1)
                        self.assertLess(len(ancestors), 6)
                        ancestors.append(cursor)
                        peers.pids.add(cursor)
                        cursor = int(subprocess.check_output(
                            ['ps', '-o', 'ppid=', '-p', str(cursor)]).strip())
                    groups = {int(subprocess.check_output(
                        ['ps', '-o', 'pgid=', '-p', str(pid)]).strip()) for pid in ancestors}
                    proc.send_signal(signum)
                    for conn, stream, _ in owned:
                        if peer_mode == 'ignore':
                            ready, _, _ = select.select([conn], [], [], 4)
                            self.assertTrue(ready, 'nested group missed cancellation')
                            self.assertEqual(stream.readline(), b'TERM\n')
                        self.assert_closed(conn, stream)
                    _, stderr = proc.communicate(timeout=5)
                    self.assertEqual(proc.returncode, 128 + signum, stderr)
                    self.assertEqual(groups | {info['pgid'] for _, _, info in owned},
                                     {ancestors[-1]})
                    self.assertEqual(hits, ['/'])
                if runtime == 'langgraph':
                    markers = list(out.glob('.inflight-*-crawl-*.json'))
                    self.assertTrue(markers, 'cancelled crawl lost its uncertainty marker')
                    resumed = subprocess.run(command + args + ['--resume'],
                                             capture_output=True, text=True, timeout=15)
                    self.assertEqual(resumed.returncode, 2, resumed.stderr)
                    self.assertIn('uncertain in-flight', resumed.stderr)
                    self.assertEqual(hits, ['/'])
                    self.assertTrue(all(p.exists() for p in markers))
        finally:
            peers.close()


if __name__ == "__main__":
    unittest.main()
