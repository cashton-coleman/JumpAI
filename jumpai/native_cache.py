"""Workspace-local native binaries, built by detached low-priority workers."""

import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import subprocess
import sys
import tempfile
import threading
import time

from .runtime import compile_cpp
from .schema import JumpError, canonical


# Changes to build policy or compiler-selection inputs invalidate cached binaries.
BUILD_POLICY = 'cpp17-O2-v1'
BUILD_ENV = ('PATH', 'SDKROOT', 'DEVELOPER_DIR', 'CPATH', 'CPLUS_INCLUDE_PATH',
             'LIBRARY_PATH', 'INCLUDE', 'LIB', 'MACOSX_DEPLOYMENT_TARGET')


def file_identity(path):
    """Return a resolved path and change-sensitive file metadata; propagate I/O errors."""
    path = Path(path)
    stat = path.stat()
    return [str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns]


def toolchain_identity(settings):
    """Identify compiler, selected driver, host, and build environment without compiling."""
    compiler = settings['cpp_compiler']
    command = [compiler] if settings['cpp_style'] == 'msvc' else [compiler, '--version']
    try:
        result = subprocess.run(command, capture_output=True, timeout=5)
        version = (result.stdout + result.stderr).decode('utf-8', errors='replace')[:8192]
        files = [file_identity(compiler)]
        # Apple compiler shims report the selected toolchain's actual installation.
        for line in version.splitlines():
            if line.startswith('InstalledDir:'):
                driver = Path(line.partition(':')[2].strip()) / 'clang'
                if driver.is_file():
                    files.append(file_identity(driver))
    except (OSError, subprocess.SubprocessError) as exc:
        raise JumpError(f'Cannot identify C++ toolchain: {exc}') from exc
    return {'files': files, 'version': version, 'style': settings['cpp_style'],
            'platform': [platform.system(), platform.machine(), platform.release()],
            'environment': {name: os.environ.get(name, '') for name in BUILD_ENV},
            'policy': BUILD_POLICY}


def cached_toolchain(home, settings):
    """Reuse compiler identification while executables, selector, and environment match."""
    path = Path(home) / 'toolchain.json'
    prior = json.loads(path.read_text()) if path.exists() else None

    def guard(identity):
        """Fingerprint only a few selection inputs; never invoke the compiler."""
        names = {settings['cpp_compiler'], *(item[0] for item in identity.get('files', []))}
        # Apple's default compiler can redirect when xcode-select changes.
        names.add('/var/db/xcode_select_link')
        return {'files': {name: file_identity(name) if Path(name).exists() else None for name in names},
                'settings': settings, 'policy': BUILD_POLICY,
                'platform': [platform.system(), platform.machine(), platform.release()],
                'environment': {name: os.environ.get(name, '') for name in BUILD_ENV}}

    if prior and prior['guard'] == guard(prior['identity']):
        return prior['identity']
    identity = toolchain_identity(settings)
    write_state(path, {'guard': guard(identity), 'identity': identity})
    return identity


def write_state(path, value):
    """Atomically replace a small JSON state file in its existing directory."""
    from .install import atomic_write
    atomic_write(path, canonical(value) + '\n')


def dependencies_match(dependencies):
    """Return false when a recorded compiler dependency changes or disappears."""
    try:
        return all(file_identity(path) == identity for path, identity in dependencies.items())
    except OSError:
        return False


def build_status(directory):
    """Read a completed build, checking dependencies and binary integrity before reuse."""
    state = directory / 'state.json'
    if not state.exists():
        return {'status': 'building', 'build_key': directory.name}
    result = json.loads(state.read_text())
    if result['status'] == 'ready':
        from .cache_guard import checked
        validation = checked(directory)
        if validation == 'unsupported':
            validation = 'ready' if dependencies_match(result.get('dependencies', {})) else 'stale'
        if validation == 'pending':
            return {'status': 'building', 'build_key': directory.name, 'validation_pending': True}
        if validation == 'stale':
            return {'status': 'stale', 'build_key': directory.name}
        binary = directory / ('program.exe' if os.name == 'nt' else 'program')
        if binary.is_symlink() or not binary.is_file() or hashlib.sha256(binary.read_bytes()).hexdigest() != result['sha256']:
            raise JumpError(f'Cached binary integrity check failed: {directory}; remove this cache entry to rebuild')
        result['binary'] = str(binary)
    return {**result, 'build_key': directory.name}


def cached_binary(home, artifact, manifest, digest, settings, hashes):
    """Reuse an exact native build or queue one worker; never wait for compilation.

    An exclusive directory claim prevents duplicate builds. Abandoned claims
    are reported after two minutes rather than silently retrying uncertain work.
    """
    identity = cached_toolchain(home, settings)
    source = {'files': hashes, 'entrypoint': manifest['entrypoint']}
    key = hashlib.sha256(canonical({'source': source, 'toolchain': identity}).encode()).hexdigest()
    directory = Path(home) / 'binaries' / key
    if directory.is_symlink() or not directory.resolve().is_relative_to(Path(home).resolve()):
        raise JumpError('Binary cache escapes catalog workspace')
    directory.mkdir(parents=True, exist_ok=True)
    status = build_status(directory)
    if status['status'] in ('ready', 'failed') or status.get('validation_pending'):
        return status

    # Only the claimant launches a worker; other callers immediately use fallback.
    claim = directory / 'building'
    try:
        claim.mkdir()
    except FileExistsError:
        if time.time() - claim.stat().st_mtime > 120:
            raise JumpError(f'Background build appears abandoned; remove {directory} to retry')
        return {'status': 'building', 'build_key': key}
    try:
        (directory / 'state.json').unlink(missing_ok=True)
        job = {'artifact': str(artifact), 'manifest': manifest, 'digest': digest,
               'settings': settings, 'toolchain': identity}
        write_state(directory / 'job.json', job)
        launcher = Path(__file__).resolve().parents[1] / 'skills/jumpai/scripts/native_build.py'
        flags = {'start_new_session': True} if os.name == 'posix' else {
            'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.BELOW_NORMAL_PRIORITY_CLASS}
        process = subprocess.Popen([sys.executable, str(launcher), str(directory)],
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, **flags)
        threading.Thread(target=process.wait, daemon=True).start()
    except OSError as exc:
        claim.rmdir()
        raise JumpError(f'Could not queue C++ build: {exc}') from exc
    return {'status': 'building', 'build_key': key}


def wait_for_build(directory):
    """Wait only for explicit verification or benchmarks; normal runs never call this."""
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        status = build_status(directory)
        if status['status'] != 'building':
            return status
        time.sleep(0.05)
    raise JumpError(f'Background build did not complete: {directory}')


def build_worker(directory):
    """Build one claimed bundle at low priority and atomically publish its result.

    Recheck source and toolchain before publication. Failures are retained for
    callers; a failed build is not repeatedly compiled by subsequent runs.
    """
    directory = Path(directory).resolve()
    started = time.monotonic()
    try:
        priority = 'below-normal'
        if os.name == 'posix':
            try:
                os.nice(10)
            except PermissionError:
                priority = 'normal: host denied lowering priority'
        job = json.loads((directory / 'job.json').read_text())
        artifact = Path(job['artifact'])
        manifest = job['manifest']
        sources = {name: (artifact / name).read_bytes() for name in manifest['files']}
        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()}
        digest = hashlib.sha256(canonical({'manifest': manifest, 'files': hashes}).encode()).hexdigest()
        if digest != job['digest']:
            raise JumpError('Source changed before background compilation')

        # Stage sources and binary privately so partial output is never executable.
        with tempfile.TemporaryDirectory(prefix='build-', dir=directory) as temp:
            build = Path(temp)
            for name in manifest['files']:
                target = build / 'source' / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(sources[name])
            binary = compile_cpp(job['settings'], build / 'source' / manifest['entrypoint'], build,
                                 dependencies=True)
            dependencies = {}
            dependency_file = build / 'dependencies.d'
            if dependency_file.exists():
                names = shlex.split(dependency_file.read_text().replace('\\\n', ' ').partition(':')[2])
                dependencies = {name: file_identity(name) for name in names
                                if not Path(name).resolve().is_relative_to(build)}
            elif (build / 'dependencies.json').exists():
                names = json.loads((build / 'dependencies.json').read_text())['Data']['Includes']
                dependencies = {name: file_identity(name) for name in names
                                if not Path(name).resolve().is_relative_to(build)}
            else:
                raise JumpError('Compiler did not emit dependency metadata; cannot safely cache this build')
            if toolchain_identity(job['settings']) != job['toolchain']:
                raise JumpError('Toolchain changed during background compilation')
            binary_hash = hashlib.sha256(binary.read_bytes()).hexdigest()
            os.replace(binary, directory / binary.name)
            write_state(directory / 'state.json', {'status': 'ready', 'sha256': binary_hash,
                        'dependencies': dependencies, 'priority': priority,
                        'build_ms': round((time.monotonic() - started) * 1000, 3)})
    except Exception as exc:
        write_state(directory / 'state.json', {'status': 'failed', 'error': str(exc)[:4096]})
    finally:
        (directory / 'building').rmdir()
