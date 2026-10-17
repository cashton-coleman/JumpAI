"""Check catalog contracts, cache behavior, and execution boundaries with isolated fixtures."""

import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from jumpai.catalog import Catalog, pointer
from jumpai.cli import main
from jumpai.runner import Runner
from jumpai.schema import JumpError, check_schema, parse_json

ROOT = Path(__file__).resolve().parents[1]
EMPTY = {'type': 'object', 'properties': {}, 'additionalProperties': False}


class CatalogTests(unittest.TestCase):
    def setUp(self):
        """Create isolated fixture files and resources used by this test case."""
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.catalog = Catalog(self.root / 'catalog')
        self.runner = Runner(self.catalog, self.root)

    def tearDown(self):
        """Release the test resources and remove temporary fixture files."""
        self.catalog.close()
        self.temp.cleanup()

    def make_program(self, name='test.echo', code='import sys\nsys.stdout.write(sys.stdin.read())\n', timeout=2, schema=None):
        """Write and register a temporary program fixture; return its manifest path."""
        source = self.root / name
        source.mkdir(exist_ok=True)
        (source / 'main.py').write_text(code)
        manifest = {'name': name, 'version': '1.0.0', 'purpose': 'Echo structured test data',
                    'runtime': 'python', 'entrypoint': 'main.py', 'files': ['main.py'],
                    'timeout_seconds': timeout, 'input_schema': schema or EMPTY, 'output_schema': schema or EMPTY}
        file = source / 'manifest.json'
        file.write_text(json.dumps(manifest))
        self.catalog.register(file)
        return file

    def verified(self):
        """Verify the temporary echo program and return its pinned target ID."""
        self.make_program()
        cases = self.root / 'cases.json'
        cases.write_text('[{"input": {}, "output": {}}]')
        self.runner.verify('test.echo@1.0.0', cases)
        return 'test.echo@1.0.0'

    def test_cache_hit_skips_search_and_survives_reopen(self):
        """Verify cache hit skips search and survives reopen."""
        target = self.verified()
        self.catalog.remember('  Summarize   Benchmarks ', target, self.root, 'schema-v1')
        self.catalog.close()
        self.catalog = Catalog(self.root / 'catalog')
        with patch.object(self.catalog, 'search', side_effect=AssertionError('cache hit searched')):
            hit = self.catalog.resolve('summarize benchmarks', self.root, 'schema-v1')
        self.assertTrue(hit['cache_hit'])
        self.assertEqual(hit['id'], target)
        self.assertEqual(self.catalog.stats()['resolution_hits'], 1)

    def test_cache_matches_unicode_caseless_queries(self):
        """Verify cache matches unicode caseless queries."""
        target = self.verified()
        for stored, lookup in [('  Straße  Reports ', 'STRASSE reports'),
                               ('ΟΣ reports', 'οσ REPORTS')]:
            with self.subTest(stored=stored, lookup=lookup):
                self.catalog.remember(stored, target, self.root, 'unicode-v1')
                with patch.object(self.catalog, 'search', side_effect=AssertionError('cache hit searched')):
                    hit = self.catalog.resolve(lookup, self.root, 'unicode-v1')
                self.assertTrue(hit['cache_hit'])
                self.assertEqual(hit['id'], target)

    def test_cache_is_scoped_and_invalidated_on_registration(self):
        """Verify cache is scoped and invalidated on registration."""
        target = self.verified()
        self.catalog.remember('echo data', target, self.root, 'v1')
        self.assertFalse(self.catalog.resolve('echo data', self.root, 'v2')['cache_hit'])
        self.assertFalse(self.catalog.resolve('echo data', self.root / 'other', 'v1')['cache_hit'])
        self.make_program('another.echo')
        self.assertFalse(self.catalog.resolve('echo data', self.root, 'v1')['cache_hit'])

    def test_cache_miss_never_selects_search_result(self):
        """Verify cache miss never selects search result."""
        self.verified()
        result = self.catalog.resolve('echo', self.root)
        self.assertFalse(result['cache_hit'])
        self.assertEqual(result['candidates'][0]['id'], 'test.echo@1.0.0')
        self.assertNotIn('id', result)

    def test_experimental_cannot_be_cached_or_run_by_default(self):
        """Verify experimental cannot be cached or run by default."""
        self.make_program()
        with self.assertRaisesRegex(JumpError, 'experimental'):
            self.catalog.remember('echo', 'test.echo@1.0.0', self.root)
        with self.assertRaisesRegex(JumpError, 'experimental'):
            self.runner.run('test.echo@1.0.0', {})
        self.assertEqual(self.runner.run('test.echo@1.0.0', {}, allow_experimental=True)['output'], {})

    def test_immutable_version_and_snapshot(self):
        """Verify immutable version and snapshot."""
        file = self.make_program()
        self.assertFalse(self.catalog.register(file)['registered'])
        (file.parent / 'main.py').write_text('raise RuntimeError("changed source")')
        self.assertEqual(self.runner.run('test.echo@1.0.0', {}, allow_experimental=True)['output'], {})
        with self.assertRaisesRegex(JumpError, 'Immutable'):
            self.catalog.register(file)

    def test_tampered_snapshot_is_rejected(self):
        """Verify tampered snapshot is rejected."""
        self.make_program()
        row = self.catalog.get('test.echo@1.0.0')
        (Path(row['artifact']) / 'main.py').write_text('print("{}")')
        with self.assertRaisesRegex(JumpError, 'integrity'):
            self.runner.run('test.echo@1.0.0', {}, allow_experimental=True)

    def test_unregistered_bundle_file_is_rejected(self):
        """Verify unregistered bundle file is rejected."""
        self.make_program()
        row = self.catalog.get('test.echo@1.0.0')
        (Path(row['artifact']) / 'unregistered.py').write_text('print("unexpected dependency")')
        with self.assertRaisesRegex(JumpError, 'inventory'):
            self.runner.run('test.echo@1.0.0', {}, allow_experimental=True)

    def test_disable_prevents_cached_and_direct_execution(self):
        """Verify disable prevents cached and direct execution."""
        target = self.verified()
        self.catalog.remember('echo', target, self.root)
        self.catalog.set_enabled(target, False)
        self.assertFalse(self.catalog.resolve('echo', self.root)['cache_hit'])
        self.assertEqual(self.catalog.search('echo'), [])
        with self.assertRaisesRegex(JumpError, 'disabled'):
            self.runner.run(target, {}, allow_experimental=True)

    def test_invalid_input_does_not_start_program(self):
        """Verify invalid input does not start program."""
        self.make_program(code='raise RuntimeError("should not execute")')
        with self.assertRaisesRegex(JumpError, 'unexpected fields'):
            self.runner.run('test.echo@1.0.0', {'bad': 1}, allow_experimental=True)
        self.assertEqual(self.catalog.stats()['runs'], 0)

    def test_bad_output_and_nonzero_exit_are_failures(self):
        """Verify bad output and nonzero exit are failures."""
        for name, code, error in [('bad.json', 'print("not-json")', 'Invalid JSON'),
                                  ('bad.schema', 'print("[]")', 'output: expected object'),
                                  ('bad.exit', 'raise RuntimeError("broken")', 'exited with code')]:
            with self.subTest(name=name):
                self.make_program(name, code)
                with self.assertRaisesRegex(JumpError, error):
                    self.runner.run(name + '@1.0.0', {}, allow_experimental=True)
        self.assertEqual(self.catalog.stats()['successful_runs'], 0)

    def test_timeout_and_output_limit(self):
        """Verify timeout and output limit."""
        self.make_program('slow', 'import time\ntime.sleep(5)', timeout=0.05)
        with self.assertRaisesRegex(JumpError, 'timeout'):
            self.runner.run('slow@1.0.0', {}, allow_experimental=True)
        self.make_program('large', 'print("x" * (3 * 1024 * 1024))')
        with self.assertRaisesRegex(JumpError, 'exceeded 2 MiB'):
            self.runner.run('large@1.0.0', {}, allow_experimental=True)

    def test_failed_verification_revokes_validation_and_cache(self):
        """Verify failed verification revokes validation and cache."""
        target = self.verified()
        self.catalog.remember('echo', target, self.root)
        cases = self.root / 'bad-cases.json'
        cases.write_text('[{"input": {}, "output": {"wrong": true}}]')
        with self.assertRaisesRegex(JumpError, 'did not match'):
            self.runner.verify(target, cases)
        self.assertFalse(self.catalog.get(target)['validated'])
        self.assertFalse(self.catalog.resolve('echo', self.root)['cache_hit'])

    def test_search_quotes_are_data_and_results_are_bounded(self):
        """Verify search quotes are data and results are bounded."""
        self.make_program()
        self.assertEqual(len(self.catalog.search('echo " OR NOT *', 1)), 1)
        self.assertEqual(self.catalog.search('"'), [])
        with self.assertRaises(JumpError):
            self.catalog.search('echo', 100)

    def test_registration_rejects_unsupported_schema_and_path_escape(self):
        """Verify registration rejects unsupported schema and path escape."""
        file = self.make_program()
        manifest = json.loads(file.read_text())
        manifest['version'] = '2.0.0'
        manifest['input_schema']['oneOf'] = []
        file.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(JumpError, 'unsupported'):
            self.catalog.register(file)
        del manifest['input_schema']['oneOf']
        manifest['files'] = ['../secret.py']
        manifest['entrypoint'] = '../secret.py'
        file.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(JumpError, 'relative file'):
            self.catalog.register(file)

    def install_examples(self):
        """Register and verify bundled examples; return a runner using the repository workspace."""
        runner = Runner(self.catalog, ROOT)
        for directory in sorted((ROOT / 'examples/programs').iterdir()):
            result = self.catalog.register(directory / 'manifest.json')
            runner.verify(result['id'], directory / 'cases.json')
        file = ROOT / 'examples/workflows/summarize-json.json'
        self.catalog.register(file)
        runner.verify('summarize.json@1.0.1', ROOT / 'examples/workflows/cases.json')
        return runner

    def test_composition_passes_outputs_and_returns_only_final_result(self):
        """Verify composition passes outputs and returns only final result."""
        runner = self.install_examples()
        result = runner.run('summarize.json@1.0.1', {'path': 'examples/measurements.json', 'field': 'duration_ms'})
        self.assertEqual(result['output'], {'count': 3, 'min': 10, 'max': 30, 'mean': 20, 'median': 20})
        children = self.catalog.db.execute('SELECT * FROM runs WHERE parent_id=?', (result['run_id'],)).fetchall()
        self.assertEqual(len(children), 2)
        self.assertTrue(all(c['success'] for c in children))
        self.assertNotIn('records', result['output'])

    def test_disabled_dependency_invalidates_workflow(self):
        """Verify disabled dependency invalidates workflow."""
        runner = self.install_examples()
        self.catalog.remember('summarize', 'summarize.json@1.0.1', ROOT)
        self.catalog.set_enabled('numbers.summary@1.0.1', False)
        self.assertFalse(self.catalog.resolve('summarize', ROOT)['cache_hit'])
        with self.assertRaisesRegex(JumpError, 'disabled'):
            runner.run('summarize.json@1.0.1', {'path': 'examples/measurements.json', 'field': 'duration_ms'})

    def test_workflow_rejects_forward_reference(self):
        """Verify workflow rejects forward reference."""
        self.install_examples()
        m = json.loads((ROOT / 'examples/workflows/summarize-json.json').read_text())
        m['version'] = '2.0.0'
        m['steps'][0]['bindings']['path'] = {'step': 'summarize', 'pointer': ''}
        file = self.root / 'bad-workflow.json'
        file.write_text(json.dumps(m))
        with self.assertRaisesRegex(JumpError, 'preceding'):
            self.catalog.register(file)

    def test_failed_step_stops_workflow(self):
        """Verify failed step stops workflow."""
        runner = self.install_examples()
        before = self.catalog.stats()['runs']
        with self.assertRaisesRegex(JumpError, 'Step load'):
            runner.run('summarize.json@1.0.1', {'path': 'does-not-exist.json', 'field': 'ms'})
        self.assertEqual(self.catalog.stats()['runs'] - before, 2)

    def test_cli_executes_remembered_query_and_does_not_execute_miss(self):
        """Verify cli executes remembered query and does not execute miss."""
        target = self.verified()
        def cli(*args):
            """Capture CLI output and return the exit status with its decoded JSON response."""
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(['--home', str(self.catalog.home), *args])
            return (code, json.loads(out.getvalue()))
        code, result = cli('run', '--target', target, '--remember', 'echo data', '--cwd', str(self.root))
        self.assertEqual(code, 0)
        self.assertIn('remembered', result)
        code, result = cli('run', '--query', 'echo data', '--cwd', str(self.root))
        self.assertEqual(code, 0)
        self.assertTrue(result['cache_hit'])
        before = self.catalog.stats()['runs']
        code, result = cli('run', '--query', 'echo missing', '--cwd', str(self.root))
        self.assertEqual(code, 3)
        self.assertFalse(result['cache_hit'])
        self.assertEqual(self.catalog.stats()['runs'], before)

    def test_failed_execution_is_not_remembered(self):
        """Verify failed execution is not remembered."""
        target = self.verified()
        with contextlib.redirect_stderr(io.StringIO()):
            code = main(['--home', str(self.catalog.home), 'run', '--target', target,
                         '--input', '{"unexpected":1}', '--remember', 'bad', '--cwd', str(self.root)])
        self.assertEqual(code, 2)
        self.assertEqual(self.catalog.stats()['resolutions'], 0)

    def test_catalog_change_during_run_prevents_stale_binding(self):
        """Verify catalog change during run prevents stale binding."""
        target = self.verified()
        revision = self.catalog.revision
        self.make_program('another')
        with self.assertRaisesRegex(JumpError, 'changed during execution'):
            self.catalog.remember('echo', target, self.root, expected_revision=revision)

    def test_runner_logs_do_not_store_inputs_outputs_or_stderr(self):
        """Verify runner logs do not store inputs outputs or stderr."""
        self.make_program(code='raise RuntimeError("private-token-123")')
        with self.assertRaises(JumpError):
            self.runner.run('test.echo@1.0.0', {}, allow_experimental=True)
        row = self.catalog.db.execute('SELECT * FROM runs').fetchone()
        self.assertNotIn('private-token', str(dict(row)))


class ContractsTests(unittest.TestCase):
    def test_strict_json(self):
        """Verify strict json."""
        for text in ('{"x":1,"x":2}', 'NaN', 'Infinity', '1e10000'):
            with self.subTest(text=text), self.assertRaises(JumpError):
                parse_json(text)

    def test_schema_rejects_silent_ignored_constraints(self):
        """Verify schema rejects silent ignored constraints."""
        for schema in ({'type':'string','minimum':0}, {'type':'array'},
                       {'type':'object','required':['missing']}, {'type':'string','pattern':'abc'}):
            with self.subTest(schema=schema), self.assertRaises(JumpError):
                check_schema(schema)

    def test_pointer_handles_escaping_and_array_bounds(self):
        """Verify pointer handles escaping and array bounds."""
        self.assertEqual(pointer({'a/b': {'~key':[42]}}, '/a~1b/~0key/0'), 42)
        for path in ('/-1', '/01', '/1'):
            with self.assertRaises(JumpError):
                pointer([42], path)

    def test_boolean_does_not_satisfy_numeric_schema(self):
        """Verify boolean does not satisfy numeric schema."""
        from jumpai.schema import validate
        with self.assertRaises(JumpError):
            validate(True, {'type':'number'})

    def test_cli_process_emits_json_error(self):
        """Verify cli process emits json error."""
        with tempfile.TemporaryDirectory() as home:
            result = subprocess.run([sys.executable, '-m', 'jumpai', '--home', home, 'inspect', 'missing@1'],
                                    cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn('Unknown version', json.loads(result.stderr)['error'])


if __name__ == '__main__':
    unittest.main()
