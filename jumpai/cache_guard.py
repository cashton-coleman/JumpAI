"""Validate native dependencies with macOS/BSD vnode events; fail closed on disconnect."""

import hashlib
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import threading
import time
import uuid


class Watch:
    """Own one dependency snapshot and its file/ancestor event subscriptions."""

    def __init__(self, dependencies):
        """Subscribe before accepting the snapshot; propagate unsupported/watch-limit errors."""
        import resource
        from .native_cache import dependencies_match

        self.queue = select.kqueue()
        self.descriptors = []
        self.valid = False
        try:
            paths = set()
            for name in dependencies:
                path = Path(name)
                # Watch files for in-place writes and ancestors for symlink/replacement changes.
                paths.update([path, *path.parents, path.resolve(), *path.resolve().parents])
            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            if soft < len(paths) * 4 + 128:
                resource.setrlimit(resource.RLIMIT_NOFILE, (min(hard, len(paths) * 4 + 128), hard))
            flags = (select.KQ_NOTE_WRITE | select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME
                     | select.KQ_NOTE_ATTRIB | select.KQ_NOTE_EXTEND | select.KQ_NOTE_REVOKE)
            for path in paths:
                descriptor = os.open(path, getattr(os, 'O_EVTONLY', os.O_RDONLY))
                self.descriptors.append(descriptor)
                self.queue.control([select.kevent(descriptor, filter=select.KQ_FILTER_VNODE,
                                                  flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                                                  fflags=flags)], 0, 0)
            self.valid = dependencies_match(dependencies)
        except BaseException:
            self.close()
            raise

    def changed(self):
        """Drain pending kernel events; a single event requires snapshot revalidation."""
        return bool(self.queue.control([], 1, 0))

    def close(self):
        """Release the event queue and every file descriptor owned by this watch."""
        self.queue.close()
        for descriptor in self.descriptors:
            os.close(descriptor)


def address(home):
    """Return a private IPC directory; reject symlinks or another user's directory."""
    key = hashlib.sha256(str(Path(home).resolve()).encode()).hexdigest()[:20]
    directory = Path('/tmp') / f'jumpai-guard-{os.getuid()}-{key}'
    directory.mkdir(mode=0o700, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid():
        raise OSError('Invalid native-cache guard directory')
    directory.chmod(0o700)
    (directory / 'requests').mkdir(exist_ok=True)
    (directory / 'responses').mkdir(exist_ok=True)
    return directory


def checked(directory):
    """Return ready/stale/pending/unsupported without scanning dependencies in the caller.

    Only a live guard that has drained its events may authorize native reuse.
    A missing or slow guard returns pending, allowing Python fallback while it starts.
    """
    if not hasattr(select, 'kqueue'):
        return 'unsupported'
    import fcntl
    from .native_cache import write_state

    request = response = None
    try:
        path = address(directory.parent.parent)
        with (path / 'lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                alive = True
            else:
                alive = False
        if not alive:
            launcher = Path(__file__).resolve().parents[1] / 'skills/jumpai/scripts/cache_guard.py'
            process = subprocess.Popen([sys.executable, str(launcher), str(directory.parent.parent)],
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL, start_new_session=True)
            threading.Thread(target=process.wait, daemon=True).start()
            return 'pending'
        name = uuid.uuid4().hex + '.json'
        request, response = path / 'requests' / name, path / 'responses' / name
        write_state(request, {'key': directory.name})
        deadline = time.monotonic() + 0.02
        while time.monotonic() < deadline:
            if response.exists():
                answer = json.loads(response.read_text())['status']
                return answer if answer in ('ready', 'stale', 'pending', 'unsupported') else 'pending'
            time.sleep(0.001)
        return 'pending'
    except (OSError, ValueError, KeyError):
        # Preserve correctness when the host blocks IPC or event monitoring.
        return 'unsupported'
    finally:
        if request:
            request.unlink(missing_ok=True)
            response.unlink(missing_ok=True)


def serve(home):
    """Serve one workspace's dependency watches; exit after 60 idle seconds.

    Revalidate changed snapshots only after replying pending. Keep at most four
    active watches; unsupported event subscriptions request conservative checks.
    """
    import fcntl
    from .native_cache import write_state

    home = Path(home).resolve()
    path = address(home)
    watches = {}
    with (path / 'lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        descriptor = os.open(path / 'requests', getattr(os, 'O_EVTONLY', os.O_RDONLY))
        queue = select.kqueue()
        try:
            queue.control([select.kevent(descriptor, filter=select.KQ_FILTER_VNODE,
                                         flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                                         fflags=select.KQ_NOTE_WRITE)], 0, 0)
            while True:
                for request in (path / 'requests').glob('*.json'):
                    refresh = None
                    try:
                        key = json.loads(request.read_text())['key']
                        if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
                            continue
                        state = home / 'binaries' / key / 'state.json'
                        stat = state.stat()
                        stamp = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                        prior = watches.get(key)
                        if prior and prior[0] == stamp and prior[1] is None:
                            answer = 'unsupported'
                        elif prior and prior[0] == stamp and not prior[1].changed():
                            answer = 'ready' if prior[1].valid else 'stale'
                        else:
                            answer = 'pending'
                            refresh = (key, state, stamp)
                        write_state(path / 'responses' / request.name, {'status': answer})
                    except (OSError, ValueError, KeyError):
                        continue
                    finally:
                        request.unlink(missing_ok=True)
                    if refresh:
                        key, state, stamp = refresh
                        prior = watches.pop(key, None)
                        if prior and prior[1]:
                            prior[1].close()
                        if len(watches) >= 4:
                            _, old = watches.popitem()
                            if old[1]:
                                old[1].close()
                        try:
                            dependencies = json.loads(state.read_text())['dependencies']
                            watch = Watch(dependencies)
                        except (OSError, ValueError, KeyError):
                            watch = None
                        watches[key] = (stamp, watch)
                if not queue.control([], 1, 60):
                    break
        finally:
            queue.close()
            os.close(descriptor)
            for _, watch in watches.values():
                if watch:
                    watch.close()
            for response in (path / 'responses').glob('*.json'):
                response.unlink(missing_ok=True)
