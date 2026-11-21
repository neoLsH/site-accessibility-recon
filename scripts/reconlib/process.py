"""POSIX CLI supervisor; SIGKILL and descendants escaping via setsid are excluded."""
import os
import signal
import subprocess
import sys
import time


# Script and arguments are separate argv entries, never interpolated into code.
_AGENT_WORKER = (
    "import os, runpy, sys; "
    "sys.argv = sys.argv[1:]; "
    "sys.path.insert(0, os.path.dirname(sys.argv[0])); "
    "sys.exit(runpy.run_path(sys.argv[0], run_name='recon_agent_worker')['main']())"
)


def agent_worker_argv(script, args):
    """Skip agent.py's __main__ supervisor when already inside our owned group."""
    return [sys.executable, '-c', _AGENT_WORKER, os.path.abspath(script), *args]


def _signal_group(proc, signum):
    try:
        os.killpg(proc.pid, signum)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        if sys.platform != 'darwin':
            raise
        # Darwin returns EPERM for zombie-only groups, even for SIGKILL.
        snapshot = subprocess.run(
            ['/bin/ps', '-A', '-o', 'pgid=,stat='], capture_output=True,
            text=True, check=True, timeout=1.0)
        for line in snapshot.stdout.splitlines():
            pgid, state = line.split()
            if int(pgid) == proc.pid and not state.startswith('Z'):
                raise
        return False


def _stop(proc):
    """Bounded TERM then KILL, including when only descendants remain."""
    _signal_group(proc, signal.SIGTERM)
    deadline = time.monotonic() + 1.0
    drained = False
    drain_error = None
    stdout = stderr = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _signal_group(proc, signal.SIGKILL)
            break
        if not drained:
            try:
                stdout, stderr = proc.communicate(timeout=min(0.05, remaining))
                drained = True
            except subprocess.TimeoutExpired:
                continue
            except BaseException as exc:
                drained = True
                drain_error = exc
        time.sleep(min(0.05, remaining))
    try:
        try:
            if not drained:
                try:
                    stdout, stderr = proc.communicate(timeout=1.0)
                except subprocess.TimeoutExpired as exc:
                    stdout, stderr = exc.output, exc.stderr
                    if proc.text_mode:
                        stdout = (stdout or b'').decode(proc.encoding, proc.errors)
                        stderr = (stderr or b'').decode(proc.encoding, proc.errors)
        finally:
            proc.wait(timeout=1.0)
        if drain_error is not None:
            raise drain_error
        return stdout, stderr
    finally:
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()


def supervised_run(argv, *, capture_output=False, text=False):
    """Run on the main thread; cancellation cleans the group before returning 128 + signal."""
    cancelled = None
    previous = {}
    proc = None
    stopping = False

    def cancel(signum, _frame):
        nonlocal cancelled
        # Record only: safe even during Popen, before proc has been assigned.
        if cancelled is None:
            cancelled = signum

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.signal(signum, cancel)
        proc = subprocess.Popen(
            argv, start_new_session=True, text=text,
            stdout=subprocess.PIPE if capture_output else None,
            stderr=subprocess.PIPE if capture_output else None)
        while not cancelled:
            try:
                stdout, stderr = proc.communicate(timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                continue
        if cancelled:
            stopping = True
            stdout, stderr = _stop(proc)
        return subprocess.CompletedProcess(
            argv, 128 + cancelled if cancelled else proc.returncode, stdout, stderr)
    except BaseException:
        if proc is not None and not stopping:
            _stop(proc)
        raise
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
