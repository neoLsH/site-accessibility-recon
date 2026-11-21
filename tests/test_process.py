"""POSIX CLI cancellation tests. READY/control sockets never share stdout pipes."""
import contextlib
import importlib
import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from support import SCRIPTS


# Control sockets prove liveness independently of inherited stdout/stderr pipes.
PEER = '''
import json, os, signal, socket, sys
s = socket.create_connection(('127.0.0.1', int(sys.argv[1])))
if sys.argv[2] == 'ignore':
    signal.signal(signal.SIGTERM, lambda *_: s.sendall(b'TERM\\n'))
s.sendall((json.dumps({'pid': os.getpid(), 'ppid': os.getppid(),
                      'pgid': os.getpgrp()}) + '\\n').encode())
while True:
    command = s.recv(1)
    if not command or command == b'x':
        break
    s.sendall(b'PONG\\n')
'''


class Peers:
    def __init__(self):
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen()
        self.listener.settimeout(10)
        self.port = self.listener.getsockname()[1]
        self.connections = []
        self.pids = set()

    def accept(self):
        conn, _ = self.listener.accept()
        conn.settimeout(5)
        stream = conn.makefile('rb')
        self.connections.append((conn, stream))
        info = json.loads(stream.readline())
        self.pids.add(info['pid'])
        return conn, stream, info

    def close(self):
        # Only kill PIDs received from our private fixture/control channel.
        for pid in self.pids:
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        for conn, stream in self.connections:
            stream.close()
            conn.close()
        self.listener.close()


@contextlib.contextmanager
def running(argv, **kwargs):
    proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, **kwargs)
    try:
        yield proc
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            stream.close()


@unittest.skipUnless(os.name == 'posix', 'POSIX process groups')
class ProcessTest(unittest.TestCase):
    def test_helper_exists(self):
        self.assertIsNotNone(importlib.util.find_spec('reconlib.process'))

    def test_capture_exit_code_output_and_handler_restoration(self):
        from reconlib.process import supervised_run
        previous = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
        def handler(*_):
            self.fail('previous handler unexpectedly invoked')
        try:
            for s in previous:
                signal.signal(s, handler)
            cmd = [sys.executable, '-c',
                   "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)"]
            result = supervised_run(cmd, capture_output=True, text=True)
            self.assertIsInstance(result, subprocess.CompletedProcess)
            self.assertEqual((result.args, result.returncode, result.stdout, result.stderr),
                             (cmd, 7, 'out\n', 'err\n'))
            for s in previous:
                self.assertIs(signal.getsignal(s), handler)
            with self.assertRaises(OSError):
                supervised_run(['/no/such/recon-cancellation-executable'])
            for s in previous:
                self.assertIs(signal.getsignal(s), handler)
        finally:
            for s, old in previous.items():
                signal.signal(s, old)

    def test_signal_during_spawn_is_recorded_before_child_assignment(self):
        from reconlib import process
        real_popen = subprocess.Popen
        children = []
        previous = {s: signal.getsignal(s) for s in (signal.SIGTERM, signal.SIGINT)}
        def spawn(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            children.append(child)
            # Deterministically deliver in the Popen-return/assignment window.
            os.kill(os.getpid(), signal.SIGTERM)
            os.kill(os.getpid(), signal.SIGINT)
            return child
        try:
            with patch.object(process.subprocess, 'Popen', side_effect=spawn):
                result = process.supervised_run(
                    [sys.executable, '-c', 'import signal; signal.pause()'],
                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 128 + signal.SIGTERM)
            self.assertIsNotNone(children[0].returncode)
            with self.assertRaises(ChildProcessError):
                os.waitpid(children[0].pid, os.WNOHANG)
            for s, old in previous.items():
                self.assertIs(signal.getsignal(s), old)
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)

    @unittest.skipUnless(sys.platform == 'darwin', 'Darwin zombie-only group EPERM')
    def test_zombie_only_group_signals_do_not_prevent_reaping(self):
        from reconlib.process import _signal_group, _stop
        with running([sys.executable, '-c', 'import sys; sys.stdin.read(1)'],
                     start_new_session=True) as proc:
            os.killpg(proc.pid, 0)
            proc.stdin.write(b'x')
            proc.stdin.flush()
            deadline = time.monotonic() + 5
            while True:
                state = subprocess.check_output(
                    ['ps', '-o', 'stat=', '-p', str(proc.pid)], text=True).strip()
                if state.startswith('Z'):
                    break
                self.assertLess(time.monotonic(), deadline, 'fixture did not become a zombie')
            for signum in (0, signal.SIGTERM, signal.SIGKILL):
                with self.subTest(signal=signum):
                    with self.assertRaises(PermissionError):
                        os.killpg(proc.pid, signum)
                    self.assertFalse(_signal_group(proc, signum))
            _stop(proc)
            with self.assertRaises(ChildProcessError):
                os.waitpid(proc.pid, os.WNOHANG)
            with self.assertRaises(ProcessLookupError):
                os.killpg(proc.pid, 0)

    def test_permission_error_for_live_group_is_not_suppressed(self):
        from reconlib.process import _signal_group
        with running([sys.executable, '-c', 'import sys; sys.stdin.read(1)'],
                     start_new_session=True) as proc:
            denied = PermissionError(1, 'fixture live signal denied')
            for signum in (0, signal.SIGTERM, signal.SIGKILL):
                with self.subTest(signal=signum), patch('os.killpg', side_effect=denied):
                    with self.assertRaises(PermissionError) as caught:
                        _signal_group(proc, signum)
                    self.assertIs(caught.exception, denied)

    def test_existence_probe_cannot_abort_escalation(self):
        from reconlib.process import _stop
        with tempfile.TemporaryDirectory() as directory:
            peer = Path(directory, 'peer.py')
            peer.write_text(PEER, encoding='utf-8')
            peers = Peers()
            killpg = os.killpg
            def deny_probe(pgid, signum):
                if signum == 0:
                    raise PermissionError(1, 'fixture existence probe denied')
                return killpg(pgid, signum)
            leader = (
                'import signal, subprocess, sys; '
                'subprocess.Popen(sys.argv[1:], stdout=subprocess.DEVNULL, '
                'stderr=subprocess.DEVNULL); signal.pause()')
            try:
                with running([sys.executable, '-c', leader, sys.executable,
                              str(peer), str(peers.port), 'ignore'],
                             start_new_session=True) as proc:
                    conn, stream, _ = peers.accept()
                    started = time.monotonic()
                    with patch('os.killpg', side_effect=deny_probe):
                        try:
                            _stop(proc)
                        except PermissionError as exc:
                            self.fail(f'existence probe aborted cleanup: {exc}')
                    self.assertEqual(stream.readline(), b'TERM\n')
                    self.assertEqual(stream.readline(), b'')
                    with self.assertRaises(ChildProcessError):
                        os.waitpid(proc.pid, os.WNOHANG)
                    self.assertLess(time.monotonic() - started, 4)
            finally:
                peers.close()

    def test_decode_error_waits_for_redirected_descendant_cleanup(self):
        from reconlib.process import _stop
        for payload in (b'\xff', b'\xe2\x82'):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                peer = Path(directory, 'peer.py')
                peer.write_text(PEER, encoding='utf-8')
                peers = Peers()
                leader = (
                    'import os, signal, subprocess, sys; '
                    f'os.write(1, {payload!r}); '
                    'subprocess.Popen(sys.argv[1:], stdout=subprocess.DEVNULL, '
                    'stderr=subprocess.DEVNULL); signal.pause()')
                try:
                    with running([sys.executable, '-c', leader, sys.executable,
                                  str(peer), str(peers.port), 'ignore'],
                                 start_new_session=True, text=True, encoding='utf-8') as proc:
                        conn, stream, _ = peers.accept()
                        started = time.monotonic()
                        with self.assertRaises(UnicodeDecodeError):
                            _stop(proc)
                        self.assertEqual(stream.readline(), b'TERM\n')
                        ready, _, _ = select.select([conn], [], [], 2)
                        self.assertTrue(ready, 'decode error escaped before group cleanup')
                        self.assertEqual(stream.readline(), b'')
                        with self.assertRaises(ChildProcessError):
                            os.waitpid(proc.pid, os.WNOHANG)
                        self.assertLess(time.monotonic() - started, 4)
                finally:
                    peers.close()

    def test_supervisor_does_not_repeat_failed_cleanup(self):
        from reconlib import process
        popen, stop = subprocess.Popen, process._stop
        children, failures = [], []
        def spawn(*args, **kwargs):
            child = popen(*args, **kwargs)
            children.append(child)
            self.assertTrue(select.select([child.stdout], [], [], 5)[0])
            os.kill(os.getpid(), signal.SIGTERM)
            return child
        def cleanup(child):
            try:
                return stop(child)
            except BaseException as exc:
                failures.append(exc)
                raise
        try:
            with patch.object(process.subprocess, 'Popen', side_effect=spawn), \
                    patch.object(process, '_stop', side_effect=cleanup) as stopped:
                with self.assertRaises(UnicodeDecodeError) as caught:
                    process.supervised_run(
                        [sys.executable, '-c',
                         "import os, signal; os.write(1, b'\\xff'); signal.pause()"],
                        capture_output=True, text=True)
            self.assertEqual(stopped.call_count, 1)
            self.assertIs(caught.exception, failures[0])
            with self.assertRaises(ChildProcessError):
                os.waitpid(children[0].pid, os.WNOHANG)
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.wait(timeout=5)
                for pipe in (child.stdout, child.stderr):
                    pipe.close()

    def test_exited_leader_and_ignored_term_descendant_holding_pipes(self):
        self.assertIsNotNone(importlib.util.find_spec('reconlib.process'))
        with tempfile.TemporaryDirectory() as directory:
            peer = Path(directory, 'peer.py')
            peer.write_text(PEER, encoding='utf-8')
            peers = Peers()
            entry = (
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "from reconlib.process import supervised_run; "
                "r=supervised_run(sys.argv[2:], capture_output=True, text=True); "
                "sys.stdout.write(r.stdout); sys.exit(r.returncode)")
            leader = (
                "import subprocess, sys; "
                "p=subprocess.Popen(sys.argv[1:]); "
                "print('leader-output', flush=True)")
            try:
                with running([sys.executable, '-c', entry, SCRIPTS,
                              sys.executable, '-c', leader, sys.executable,
                              str(peer), str(peers.port), 'ignore']) as proc:
                    conn, stream, info = peers.accept()
                    # PGID retains the leader's PID even if READY arrives after reparenting.
                    deadline = time.monotonic() + 5
                    while True:
                        state = subprocess.run(['ps', '-o', 'stat=', '-p', str(info['pgid'])],
                                               capture_output=True, text=True).stdout.strip()
                        if not state or state.startswith('Z'):
                            break
                        self.assertLess(time.monotonic(), deadline, 'leader did not exit')
                    started = time.monotonic()
                    proc.send_signal(signal.SIGTERM)
                    self.assertEqual(stream.readline(), b'TERM\n')
                    proc.send_signal(signal.SIGINT)
                    proc.send_signal(signal.SIGTERM)
                    stdout, stderr = proc.communicate(timeout=5)
                    self.assertEqual(proc.returncode, 143, stderr)
                    self.assertIn(b'leader-output', stdout)
                    self.assertEqual(stream.readline(), b'')
                    self.assertLess(time.monotonic() - started, 4)
            finally:
                peers.close()


if __name__ == '__main__':
    unittest.main()
