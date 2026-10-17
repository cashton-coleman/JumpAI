"""Focused checks for toolchain detection, published settings, and native fallback."""

import json
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import time

from jumpai.catalog import Catalog
from jumpai.runner import Runner
from jumpai.runtime import DEFAULT_EXECUTION, detect_execution
from jumpai.schema import JumpError
from jumpai.native_cache import cached_toolchain, dependencies_match, file_identity, toolchain_identity, wait_for_build
from jumpai.workspace import open_workspace, publish


class RuntimeTests(unittest.TestCase):
    """Keep detection and execution checks isolated from the installed catalog."""

    def test_missing_and_broken_compilers_default_to_python(self):
        """Missing or failing toolchains fall back; failure diagnostics stay visible."""
        with patch('jumpai.runtime.shutil.which', return_value=None):
            settings, failures = detect_execution()
        self.assertEqual(settings, DEFAULT_EXECUTION)
        self.assertEqual(failures, [])

        with patch('jumpai.runtime.shutil.which', return_value='/unavailable/c++'), \
                patch('jumpai.runtime.compile_cpp', side_effect=JumpError('broken toolchain')):
            settings, failures = detect_execution()
        self.assertEqual(settings, DEFAULT_EXECUTION)
        self.assertEqual(len(failures), 1)
        self.assertIn('broken toolchain', failures[0])

    def test_settings_publish_refresh_and_python_fallback(self):
        """Persist preference, refresh a workspace, and execute the bundled Python fallback."""
        with tempfile.TemporaryDirectory(prefix='jumpai-runtime-test-') as temp, ExitStack() as resources:
            root = Path(temp)
            catalog = Catalog(root / 'catalog')
            resources.callback(catalog.close)
            manifest = self.make_bundle(root)
            catalog.set_execution_settings(dict(DEFAULT_EXECUTION))
            stored = catalog.db.execute("SELECT value FROM settings WHERE key='execution'").fetchone()
            self.assertEqual(json.loads(stored[0]), DEFAULT_EXECUTION)
            catalog.register(manifest)
            library = publish(catalog.home)
            workspace = open_workspace(library, root)
            self.assertEqual(workspace.execution_settings(), DEFAULT_EXECUTION)
            self.assertEqual(Runner(workspace, root).run('test.native@1', {}, allow_experimental=True)['output'], {})
            workspace.close()

            # A published preference update reaches existing workspace databases.
            configured = {**DEFAULT_EXECUTION, 'cpp_compiler': str(root / 'compiler')}
            catalog.set_execution_settings(configured)
            publish(catalog.home)
            workspace = open_workspace(library, root)
            resources.callback(workspace.close)
            self.assertEqual(workspace.execution_settings(), configured)
            with self.assertRaises(JumpError):
                catalog.set_execution_settings({**DEFAULT_EXECUTION, 'preferred_runtime': 'cpp'})

    def test_real_native_execution_and_compile_error(self):
        """Queue a real build, reuse it, and retain failure without repeated compilation."""
        settings, failures = detect_execution()
        if settings['preferred_runtime'] != 'cpp':
            self.skipTest(f'No usable C++ toolchain: {failures}')
        with tempfile.TemporaryDirectory(prefix='jumpai-native-test-') as temp, ExitStack() as resources:
            root = Path(temp)
            catalog = Catalog(root / 'catalog')
            resources.callback(catalog.close)
            catalog.set_execution_settings(settings)
            catalog.register(self.make_bundle(root))
            runner = Runner(catalog, root)
            result = runner.run('test.native@1', {'answer': 42}, allow_experimental=True)
            self.assertEqual(result['output'], {'answer': 42})
            self.assertEqual(result['native_build']['status'], 'building')
            directory = catalog.home / 'binaries' / result['native_build']['build_key']
            completed = wait_for_build(directory)
            self.assertEqual(completed['status'], 'ready', completed)
            self.assertTrue(completed['dependencies'])
            state = (directory / 'state.json').read_bytes()
            # A live dependency guard must avoid both full scans and compiler queries.
            from jumpai.cache_guard import checked
            if checked(directory) == 'ready':
                with patch('jumpai.native_cache.toolchain_identity', side_effect=AssertionError('compiler queried')), \
                        patch('jumpai.native_cache.dependencies_match', side_effect=AssertionError('headers scanned')):
                    result = runner.run('test.native@1', {'answer': 42}, allow_experimental=True)
            else:
                result = runner.run('test.native@1', {'answer': 42}, allow_experimental=True)
            self.assertEqual(result['native_build']['status'], 'ready')
            self.assertEqual(result['output'], {'answer': 42})
            self.assertEqual((directory / 'state.json').read_bytes(), state)

            # Metadata-only registration must reuse the existing compiled source.
            manifest = json.loads((root / 'manifest.json').read_text())
            manifest['version'] = 'metadata-only'
            (root / 'manifest.json').write_text(json.dumps(manifest))
            catalog.register(root / 'manifest.json')
            reused = runner.run('test.native@metadata-only', {}, allow_experimental=True)
            self.assertEqual(reused['native_build']['build_key'], result['native_build']['build_key'])
            self.assertEqual(reused['native_build']['status'], 'ready')

            # Register a separate broken version rather than mutating an immutable bundle.
            (root / 'main.cpp').write_text('this is not C++\n')
            manifest = json.loads((root / 'manifest.json').read_text())
            manifest['version'] = '2'
            (root / 'manifest.json').write_text(json.dumps(manifest))
            catalog.register(root / 'manifest.json')
            pending = runner.run('test.native@2', {}, allow_experimental=True)
            self.assertNotEqual(result['native_build']['build_key'], pending['native_build']['build_key'])
            failed = catalog.home / 'binaries' / pending['native_build']['build_key']
            self.assertEqual(wait_for_build(failed)['status'], 'failed')
            fallback = runner.run('test.native@2', {}, allow_experimental=True)
            self.assertEqual(fallback['output'], {})
            self.assertIn('compilation failed', fallback['native_build']['error'])
            cases = root / 'cases.json'
            cases.write_text('[{"input":{},"output":{}}]')
            with self.assertRaisesRegex(JumpError, 'compilation failed'):
                runner.verify('test.native@2', cases)

    def test_toolchain_identity_changes_with_compiler_or_environment(self):
        """Compiler replacement and changed build environment must invalidate reuse."""
        with tempfile.TemporaryDirectory() as temp:
            compiler = Path(temp) / 'c++'
            compiler.write_bytes(b'compiler-one')
            settings = {**DEFAULT_EXECUTION, 'cpp_compiler': str(compiler)}
            result = subprocess.CompletedProcess([], 0, b'example compiler', b'')
            with patch('jumpai.native_cache.subprocess.run', return_value=result):
                original = toolchain_identity(settings)
                dependencies = {str(compiler): file_identity(compiler)}
                compiler.write_bytes(b'compiler-two-with-a-new-size')
                self.assertNotEqual(original, toolchain_identity(settings))
                self.assertFalse(dependencies_match(dependencies))
                changed = toolchain_identity(settings)
                with patch.dict('os.environ', {'SDKROOT': '/another/sdk'}):
                    self.assertNotEqual(changed, toolchain_identity(settings))

    def test_native_only_pending_calls_queue_one_worker(self):
        """Repeated native-only calls return promptly and do not duplicate a pending build."""
        with tempfile.TemporaryDirectory() as temp, ExitStack() as resources:
            root = Path(temp)
            catalog = Catalog(root / 'catalog')
            resources.callback(catalog.close)
            path = self.make_bundle(root)
            manifest = json.loads(path.read_text())
            del manifest['python_fallback']
            path.write_text(json.dumps(manifest))
            catalog.register(path)
            catalog.set_execution_settings({**DEFAULT_EXECUTION, 'preferred_runtime': 'cpp',
                                            'cpp_compiler': str(root / 'compiler')})
            with patch('jumpai.native_cache.toolchain_identity', return_value={}), \
                    patch('jumpai.native_cache.subprocess.Popen') as launch:
                for _ in range(2):
                    with self.assertRaisesRegex(JumpError, 'build pending'):
                        Runner(catalog, root).run('test.native@1', {}, allow_experimental=True)
                launch.assert_called_once()

    def test_cached_compiler_identification_rechecks_changed_executable(self):
        """Warm identification skips subprocesses, but executable edits force reidentification."""
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            compiler = home / 'compiler'
            compiler.write_text('old')
            settings = {**DEFAULT_EXECUTION, 'cpp_compiler': str(compiler)}
            with patch('jumpai.native_cache.toolchain_identity', return_value={'files': []}) as identify:
                cached_toolchain(home, settings)
                cached_toolchain(home, settings)
                self.assertEqual(identify.call_count, 1)
                compiler.write_text('new')
                cached_toolchain(home, settings)
                self.assertEqual(identify.call_count, 2)

    def test_dependency_guard_detects_in_place_edits_and_replacements(self):
        """Kernel notifications must detect edits even when parent directory stamps stay unchanged."""
        from jumpai.cache_guard import checked

        def settled(directory):
            """Wait only in this explicit watcher test for background registration to finish."""
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                status = checked(directory)
                if status != 'pending':
                    return status
                time.sleep(0.05)
            self.fail('Dependency watcher did not settle')

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            header = root / 'header.h'
            header.write_text('old')
            directory = root / 'catalog/binaries' / ('a' * 64)
            directory.mkdir(parents=True)
            state = directory / 'state.json'
            state.write_text(json.dumps({'dependencies': {str(header): file_identity(header)}}))
            status = settled(directory)
            if status == 'unsupported':
                self.skipTest('Host lacks supported native dependency watches')
            self.assertEqual(status, 'ready')
            parent_stamp = root.stat().st_mtime_ns
            header.write_text('new')
            self.assertEqual(root.stat().st_mtime_ns, parent_stamp)
            self.assertEqual(settled(directory), 'stale')

            state.write_text(json.dumps({'dependencies': {str(header): file_identity(header)}}))
            self.assertEqual(settled(directory), 'ready')
            replacement = root / 'replacement.h'
            replacement.write_text('replacement')
            replacement.replace(header)
            self.assertEqual(settled(directory), 'stale')

    @staticmethod
    def make_bundle(root):
        """Write equivalent JSON echo implementations and return their manifest path."""
        (root / 'main.cpp').write_text(
            '// Echo JSON bytes without interpreting them.\n#include <iostream>\n'
            '// Copy standard input to standard output; report stream failure.\n'
            'int main() { std::cout << std::cin.rdbuf(); return std::cout ? 0 : 1; }\n'
        )
        (root / 'fallback.py').write_text('"""Echo JSON bytes through Python."""\nimport sys\nsys.stdout.write(sys.stdin.read())\n')
        manifest = {'name': 'test.native', 'version': '1', 'purpose': 'Echo JSON',
                    'runtime': 'cpp', 'entrypoint': 'main.cpp', 'python_fallback': 'fallback.py',
                    'files': ['main.cpp', 'fallback.py'],
                    'input_schema': {'type': 'object'}, 'output_schema': {'type': 'object'}}
        path = root / 'manifest.json'
        path.write_text(json.dumps(manifest))
        return path
