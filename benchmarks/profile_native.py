"""Separate native launch, cache validation, and runner costs without recompilation."""

import hashlib
import json
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch


def timed(function):
    """Return elapsed milliseconds and a successful call's value; propagate failures."""
    started = time.perf_counter()
    value = function()
    return (time.perf_counter() - started) * 1000, value


def summarize(samples):
    """Retain every observation alongside the median instead of hiding first-launch cost."""
    return {'samples_ms': samples, 'median_ms': statistics.median(samples)}


def main():
    """Profile an existing report binary and Python fallback on identical JSON input."""
    args = json.load(sys.stdin)
    sys.path.insert(0, args['root'])
    from jumpai.catalog import Catalog
    from jumpai.runner import Runner
    from jumpai import cache_guard, native_cache

    payload = json.loads(Path(args['payload']).read_text())
    encoded = json.dumps(payload, ensure_ascii=False).encode()
    original = Path(args['catalog'])
    states = list((original / 'binaries').glob('*/state.json'))
    if len(states) != 1:
        raise RuntimeError('Expected exactly one prepared cached report binary')
    original_state = states[0].read_bytes()
    state = json.loads(original_state)
    binary = states[0].parent / ('program.exe' if sys.platform == 'win32' else 'program')
    if hashlib.sha256(binary.read_bytes()).hexdigest() != state['sha256']:
        raise RuntimeError('Original cached binary integrity mismatch')

    # Copy writable catalog state; keep original benchmark observations intact.
    with tempfile.TemporaryDirectory(prefix='jumpai-native-profile-') as temp:
        root = Path(temp)
        shutil.copytree(original, root / 'catalog')
        catalog = Catalog(root / 'catalog')
        try:
            row = catalog.get('reports.combine@1.1.0')
            manifest = json.loads(row['manifest'])
            python = Path(row['artifact']) / manifest['python_fallback']
            runner = Runner(catalog, root)
            settings = catalog.execution_settings()

            def launch(command):
                """Measure spawn-to-exit with identical pipes and check semantic JSON equality."""
                process = subprocess.run(command, input=encoded, capture_output=True, timeout=10, check=True)
                if json.loads(process.stdout) != payload:
                    raise RuntimeError('Direct program output mismatch')

            direct = {'python': [], 'cpp': []}
            # Warm both executable paths before recording process timings.
            launch([sys.executable, '-B', str(python)])
            launch([str(binary)])
            for index in range(5):
                order = ('python', 'cpp') if index % 2 == 0 else ('cpp', 'python')
                for backend in order:
                    command = [sys.executable, '-B', str(python)] if backend == 'python' else [str(binary)]
                    elapsed, _ = timed(lambda: launch(command))
                    direct[backend].append(elapsed)

            # Warm the copied catalog's actual binary and dependency subscriptions.
            directory = catalog.home / 'binaries' / states[0].parent.name
            catalog.set_execution_settings({**settings, 'preferred_runtime': 'cpp'})
            native_cache.cached_toolchain(catalog.home, catalog.execution_settings())
            if native_cache.wait_for_build(directory)['status'] != 'ready':
                raise RuntimeError('Prepared binary is stale')
            for _ in range(2):
                warmed = runner.run('reports.combine@1.1.0', payload)
                if warmed['native_build']['status'] != 'ready':
                    raise RuntimeError('Warmup unexpectedly fell back to Python')
            catalog.set_execution_settings({**settings, 'preferred_runtime': 'python'})
            runner.run('reports.combine@1.1.0', payload)

            # Instrument cache checks and runner execution separately, preserving all work.
            measurements = []
            identity = native_cache.toolchain_identity
            dependencies = native_cache.dependencies_match
            cached_identity = native_cache.cached_toolchain
            guard = cache_guard.checked
            execute = runner._execute
            for index in range(3):
                order = ('python', 'cpp_old_checks', 'cpp')
                for backend in (order if index % 2 == 0 else reversed(order)):
                    catalog.set_execution_settings({**settings, 'preferred_runtime': 'python' if backend == 'python' else 'cpp'})
                    parts = {}

                    def measure(name, function):
                        """Wrap one profiled boundary, recording its inclusive elapsed time."""
                        def wrapped(*values, **options):
                            """Execute once and retain measured latency without altering the result."""
                            elapsed, value = timed(lambda: function(*values, **options))
                            parts[name] = parts.get(name, 0) + elapsed
                            return value
                        return wrapped

                    def selected_identity(home, options):
                        """Reproduce the old compiler-query path only for the control condition."""
                        return identity(options) if backend == 'cpp_old_checks' else cached_identity(home, options)

                    def selected_guard(path):
                        """Force original full header validation only for the control condition."""
                        return 'unsupported' if backend == 'cpp_old_checks' else guard(path)

                    with patch.object(native_cache, 'toolchain_identity', wraps=identity) as queries, \
                            patch.object(native_cache, 'cached_toolchain', measure('toolchain_guard_ms', selected_identity)), \
                            patch.object(cache_guard, 'checked', measure('dependency_guard_ms', selected_guard)), \
                            patch.object(native_cache, 'dependencies_match', wraps=measure('dependencies_ms', dependencies)) as scans, \
                            patch.object(runner, '_execute', measure('execute_ms', execute)):
                        elapsed, result = timed(lambda: runner.run('reports.combine@1.1.0', payload))
                        parts['compiler_queries'] = queries.call_count + (backend == 'cpp_old_checks')
                        parts['foreground_header_scans'] = scans.call_count
                    if result['output'] != payload:
                        raise RuntimeError('Runner output mismatch')
                    if backend != 'python' and result['native_build']['status'] != 'ready':
                        raise RuntimeError('Native profile must use a ready cache, not compile or fall back')
                    measurements.append({'backend': backend, 'total_ms': elapsed, **parts})

            copied_state = root / 'catalog/binaries' / states[0].parent.name / 'state.json'
            if copied_state.read_bytes() != original_state or states[0].read_bytes() != original_state:
                raise RuntimeError('Build state changed during profiling')
            print(json.dumps({'direct_spawn_to_exit': {name: summarize(values) for name, values in direct.items()},
                              'runner': measurements,
                              'runner_median_ms': {backend: statistics.median(item['total_ms'] for item in measurements
                                                                            if item['backend'] == backend)
                                                   for backend in ('python', 'cpp_old_checks', 'cpp')},
                              'dependency_count': len(state['dependencies']), 'payload_bytes': len(encoded),
                              'binary_bytes': binary.stat().st_size, 'outputs_match': True,
                              'build_state_unchanged': True,
                              'scope': 'Direct timing includes OS process startup, I/O, and JSON result validation; not pure algorithm CPU time.'}))
        finally:
            catalog.close()


if __name__ == '__main__':
    main()
