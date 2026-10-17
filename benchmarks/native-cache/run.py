"""Compare repeated foreground compilation with cold and warm background caching."""

import json
from pathlib import Path
import statistics
import sys
import tempfile
import time


def main():
    """Run one small JSON-echo comparison in disposable storage; print actual timings."""
    inputs = json.load(sys.stdin)
    sys.path.insert(0, inputs['root'])
    from jumpai.catalog import Catalog
    from jumpai.native_cache import wait_for_build
    from jumpai.runner import Runner
    from jumpai.runtime import compile_cpp
    from jumpai.workspace import read_library

    settings = read_library(inputs['library'])[1]['execution']
    if settings['preferred_runtime'] != 'cpp':
        raise RuntimeError('This comparison requires an installed C++ toolchain')

    # Both paths run the same immutable JSON echo program and validate its output.
    with tempfile.TemporaryDirectory(prefix='jumpai-cache-comparison-') as temp:
        root = Path(temp)
        (root / 'main.cpp').write_text(
            '// Echo JSON bytes.\n#include <iostream>\n'
            '// Copy standard input and report output failure.\n'
            'int main() { std::cout << std::cin.rdbuf(); return std::cout ? 0 : 1; }\n')
        (root / 'main.py').write_text('"""Echo JSON bytes."""\nimport sys\nsys.stdout.write(sys.stdin.read())\n')
        manifest = {'name': 'compare.echo', 'version': '1', 'purpose': 'Compare native build reuse',
                    'runtime': 'cpp', 'entrypoint': 'main.cpp', 'python_fallback': 'main.py',
                    'files': ['main.cpp', 'main.py'],
                    'input_schema': {'type': 'object'}, 'output_schema': {'type': 'object'}}
        (root / 'manifest.json').write_text(json.dumps(manifest))
        catalog = Catalog(root / 'catalog')
        try:
            catalog.set_execution_settings(settings)
            catalog.register(root / 'manifest.json')
            runner = Runner(catalog, root)
            artifact = Path(catalog.get('compare.echo@1')['artifact'])
            payload = {'values': list(range(100))}

            # Reproduce the prior build-per-invocation command path three times.
            baseline = []
            for _ in range(3):
                started = time.perf_counter()
                with tempfile.TemporaryDirectory(dir=root) as build:
                    binary = compile_cpp(settings, artifact / 'main.cpp', build)
                    output = runner._execute([str(binary)], manifest, payload)
                if output != payload:
                    raise RuntimeError('Uncached output mismatch')
                baseline.append((time.perf_counter() - started) * 1000)

            # Cold execution must return through Python while the worker builds.
            cold = runner.run('compare.echo@1', payload, allow_experimental=True)
            directory = catalog.home / 'binaries' / cold['native_build']['build_key']
            ready = wait_for_build(directory)
            if ready['status'] != 'ready':
                raise RuntimeError(str(ready))
            original_state = (directory / 'state.json').read_bytes()
            warm = [runner.run('compare.echo@1', payload, allow_experimental=True) for _ in range(3)]
            if any(run['output'] != payload for run in [cold, *warm]):
                raise RuntimeError('Cached/fallback output mismatch')
            if any(run['native_build']['status'] != 'ready' for run in warm):
                raise RuntimeError('Warm execution missed its cache')
            if (directory / 'state.json').read_bytes() != original_state:
                raise RuntimeError('Warm runs changed build state')
            print(json.dumps({
                'task': 'JSON echo, one cold invocation and three warm invocations',
                'uncached_ms': baseline, 'uncached_median_ms': statistics.median(baseline),
                'cold_foreground_ms': cold['duration_ms'], 'cold_status': cold['native_build']['status'],
                'background_build_ms': ready['build_ms'], 'worker_priority': ready['priority'],
                'warm_ms': [run['duration_ms'] for run in warm],
                'warm_median_ms': statistics.median(run['duration_ms'] for run in warm),
                'outputs_match': True, 'warm_build_state_unchanged': True,
                'cache_entries': len(list((catalog.home / 'binaries').iterdir())),
                'note': 'Runner timings include cache lookup and integrity checks; no model-token claim.',
            }))
        finally:
            catalog.close()


if __name__ == '__main__':
    main()
