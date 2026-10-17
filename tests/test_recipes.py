"""Focused tests for saved invocation replay and its persistence boundary."""

import contextlib
import io
import json
import unittest
from unittest.mock import patch

from test_jumpai import CatalogTests
from jumpai.catalog import Catalog
from jumpai.cli import main
from jumpai.schema import JumpError


class RecipeTests(unittest.TestCase):
    setUp = CatalogTests.setUp
    tearDown = CatalogTests.tearDown
    make_program = CatalogTests.make_program
    verified = CatalogTests.verified

    def cli(self, *args):
        """Capture CLI output and return the exit status with its decoded JSON response."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status = main(['--home', str(self.catalog.home), *args, '--cwd', str(self.root)])
        return (status, json.loads(output.getvalue()))

    def test_saved_command_replays_without_discovery_or_contract_reads(self):
        """Verify saved command replays without discovery or contract reads."""
        target = self.verified()
        status, result = self.cli('run', '--target', target, '--save-recipe', 'echo')
        self.assertEqual(status, 0)
        self.assertIn('jump echo', result['recipe']['command'])
        with patch.object(Catalog, 'search', side_effect=AssertionError('search')), \
             patch.object(Catalog, 'contract', side_effect=AssertionError('contract')), \
             patch.object(Catalog, 'resolve', side_effect=AssertionError('resolve')):
            status, result = self.cli('jump', 'echo')
        self.assertEqual(status, 0)
        self.assertTrue(result['cache_hit'])
        self.assertEqual(result['output'], {})
        self.assertEqual(self.catalog.stats()['recipe_hits'], 1)

    def test_missing_scoped_stale_and_concurrent_save_do_not_execute(self):
        """Verify missing scoped stale and concurrent save do not execute."""
        target = self.verified()
        self.cli('run', '--target', target, '--save-recipe', 'echo')
        self.assertIsNone(self.catalog.recipe('echo', self.root / 'other'))
        self.assertIsNone(self.catalog.recipe('echo', self.root, 'other-context'))
        revision = self.catalog.revision
        self.catalog.set_enabled(target, False)
        runs = self.catalog.stats()['runs']
        self.assertEqual(self.cli('jump', 'echo')[0], 3)
        self.assertEqual(self.catalog.stats()['runs'], runs)
        self.catalog.set_enabled(target, True)
        with self.assertRaisesRegex(JumpError, 'changed'):
            self.catalog.save_recipe('echo', target, {}, self.root, '', revision)

    def test_failed_or_experimental_runs_do_not_save_and_overrides_are_checked(self):
        """Verify failed or experimental runs do not save and overrides are checked."""
        target = self.verified()
        self.assertEqual(self.cli('run', '--target', target, '--input', '{"bad":1}', '--save-recipe', 'bad')[0], 2)
        self.assertEqual(self.cli('run', '--target', target, '--allow-experimental', '--save-recipe', 'bad')[0], 2)
        self.assertEqual(self.catalog.stats()['recipes'], 0)
        self.cli('run', '--target', target, '--save-recipe', 'echo')
        self.assertEqual(self.cli('jump', 'echo', '--with', '{"bad":1}')[0], 2)
        self.assertEqual(self.cli('jump', 'echo', '--with', '[]')[0], 2)

    def test_replay_executes_current_inputs_and_overrides_do_not_mutate_recipe(self):
        """Verify replay executes current inputs and overrides do not mutate recipe."""
        schema = {'type': 'object', 'properties': {'value': {'type': 'string'}}, 'required': ['value'], 'additionalProperties': False}
        code = 'import json,sys\nfrom pathlib import Path\nd=json.load(sys.stdin)\nd["value"]=Path(d["value"]).read_text()\nprint(json.dumps(d))\n'
        self.make_program(code=code, schema=schema)
        self.catalog.set_validated('test.echo@1.0.0', True)
        path = self.root / 'value.txt'
        path.write_text('first')
        self.cli('run', '--target', 'test.echo@1.0.0', '--input', json.dumps({'value': str(path)}), '--save-recipe', 'read')
        path.write_text('second')
        self.assertEqual(self.cli('jump', 'read')[1]['output'], {'value': 'second'})
        other = self.root / 'other.txt'
        other.write_text('override')
        self.assertEqual(self.cli('jump', 'read', '--with', json.dumps({'value': str(other)}))[1]['output'], {'value': 'override'})
        self.assertEqual(self.cli('jump', 'read')[1]['output'], {'value': 'second'})

    def test_version_one_migrates_preserving_cached_bindings(self):
        """Verify version one migrates preserving cached bindings."""
        target = self.verified()
        self.catalog.remember('echo', target, self.root)
        with self.catalog.db:
            self.catalog.db.execute('DROP TABLE recipes')
            self.catalog.db.execute("UPDATE meta SET value=1 WHERE key='schema_version'")
        self.catalog.close()
        self.catalog = Catalog(self.root / 'catalog')
        self.assertTrue(self.catalog.resolve('echo', self.root)['cache_hit'])
        self.assertEqual(self.catalog.stats()['recipes'], 0)
        self.assertEqual(self.catalog.db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], 3)


# Avoid collecting the imported fixture class as part of this limited suite.
del CatalogTests
