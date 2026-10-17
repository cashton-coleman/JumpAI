"""Focused checks for read-only libraries, isolated state, and child handoffs."""

import hashlib
import json
from pathlib import Path
import shlex
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from jumpai.catalog import Catalog
from jumpai.install import seed
from jumpai.schema import JumpError
from jumpai.workspace import handoff, open_workspace, publish

# Use the checkout launcher while keeping all writable fixtures temporary.
REPO = Path(__file__).resolve().parents[1]
LAUNCHER = REPO / 'skills/jumpai/scripts/jumpai.py'


class WorkspaceTests(unittest.TestCase):
    """Exercise workspace boundaries with disposable installed-library fixtures."""

    def setUp(self):
        """Publish a fixture library and create a broken sample in an isolated task."""
        self.temp = tempfile.TemporaryDirectory(prefix='jumpai-workspace-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.shared = self.root / 'shared'
        seed(self.shared)
        self.library = Path(publish(self.shared))
        self.work = self.root / 'work'
        self.work.mkdir()
        (self.work / 'sample.py').write_text('def normalize(value):\n    return value\n')
        (self.work / 'test_sample.py').write_text('import unittest\nfrom sample import normalize\nclass Check(unittest.TestCase):\n    def test_ok(self):\n        self.assertEqual(normalize(" a "), "a")\n')

    def run_cli(self, arguments):
        """Run the installed-style CLI and return JSON, failing on a nonzero exit."""
        result = subprocess.run([sys.executable, str(LAUNCHER), '--library', str(self.library), '--workspace', str(self.work), *arguments],
            cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_readonly_library_and_concrete_child_parent_checkpoint_handoff(self):
        """Verify child diagnosis and parent repair never modify the shared library."""
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in self.shared.rglob('*') if p.is_file()}
        for p in self.shared.rglob('*'):
            p.chmod(365 if p.is_dir() else 292)
        self.shared.chmod(365)
        try:
            card = self.run_cli(['handoff', '--read-only', '--source', 'sample.py', '--tests', '["test_sample.Check"]'])
            self.assertFalse(card['source_edits_allowed'])
            child = subprocess.run(shlex.split(card['commands']['diagnose']), cwd=self.root, capture_output=True, text=True)
            self.assertEqual(child.returncode, 0, child.stderr)
            receipt = json.loads(child.stdout)
            self.assertEqual(receipt['status'], 'needs_decision')
            self.assertEqual((self.work / 'sample.py').read_text(), 'def normalize(value):\n    return value\n')
            parent = self.run_cli(['resume', receipt['session_id'], '--step', 'repair', '--decision', json.dumps({'path':'sample.py','old':'return value','new':'return value.strip()'})])
            self.assertEqual(parent['summary']['final']['passed'], 1)
            self.assertTrue(parent['summary']['repair_applied'])
            self.assertTrue(Path(parent['catalog_home']).is_relative_to(self.work))
            after = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in self.shared.rglob('*') if p.is_file()}
            self.assertEqual(before, after)
        finally:
            self.shared.chmod(493)
            for p in self.shared.rglob('*'):
                p.chmod(493 if p.is_dir() else 420)

    def test_publish_excludes_private_state_and_workspaces_isolate_cache(self):
        """Ensure exported definitions omit secrets and caches remain workspace-local."""
        shared = Catalog(self.shared)
        try:
            shared.save_recipe('private', 'dev.source-snapshot@1.1.1', {'secret':'not-for-export'}, self.work, '', shared.revision)
        finally:
            shared.close()
        publish(self.shared)
        self.assertNotIn('not-for-export', self.library.read_text())
        a = open_workspace(self.library, self.work)
        other = self.root / 'other'
        other.mkdir()
        b = open_workspace(self.library, other)
        try:
            self.assertEqual(a.db.execute('SELECT count(*) FROM recipes').fetchone()[0], 0)
            a.remember('source inspection', 'dev.source-snapshot@1.1.1', self.work)
            revision = a.revision
            self.assertFalse(b.resolve('source inspection', other)['cache_hit'])
        finally:
            a.close()
            b.close()
        a = open_workspace(self.library, self.work)
        try:
            self.assertEqual(a.revision, revision)
            self.assertTrue(a.resolve('source inspection', self.work)['cache_hit'])
        finally:
            a.close()

    def test_prepared_handoff_runs_checks_once_and_resumes_without_child_tools(self):
        """Prepare complete evidence, resume once, and skip delegation for healthy code."""
        card = self.run_cli(['handoff', '--prepare', '--source', 'sample.py',
                             '--tests', '["test_sample.Check"]'])
        self.assertEqual(card['status'], 'needs_decision')
        self.assertNotIn('commands', card)
        context = card['child_assignment']['checkpoint']['context']
        self.assertEqual((context['initial_tests'], context['initial_failures']), (1, 1))
        self.assertIn('return value', context['evidence']['source'][0]['source'])

        # The child supplies judgment only; the parent uses the exact continuation.
        decision = {'path': 'sample.py', 'old': 'return value', 'new': 'return value.strip()'}
        completed = subprocess.run(
            shlex.split(card['resume_command']) + ['--decision', json.dumps(decision)],
            cwd=self.root, text=True, capture_output=True, check=True,
        )
        self.assertEqual(json.loads(completed.stdout)['summary']['final']['passed'], 1)
        catalog = open_workspace(self.library, self.work)
        self.addCleanup(catalog.close)
        checks = catalog.db.execute(
            "SELECT count(*) FROM runs WHERE target_id='triage.run-checks@1.0.1'"
        ).fetchone()[0]
        self.assertEqual(checks, 2)

        healthy = self.run_cli(['handoff', '--prepare', '--source', 'sample.py',
                                '--tests', '["test_sample.Check"]'])
        self.assertEqual(healthy['status'], 'completed')
        self.assertNotIn('child_assignment', healthy)
        with self.assertRaises(JumpError):
            handoff(catalog, self.library, self.work, [], 'sample.py', True, prepare=True)

    def test_publish_updates_availability_without_erasing_local_state(self):
        """Apply published disablement without deleting local resolution records."""
        local = open_workspace(self.library, self.work)
        try:
            local.remember('source inspection', 'dev.source-snapshot@1.1.1', self.work)
            revision = local.revision
        finally:
            local.close()
        shared = Catalog(self.shared)
        try:
            shared.set_enabled('dev.source-snapshot@1.1.1', False)
        finally:
            shared.close()
        publish(self.shared)
        local = open_workspace(self.library, self.work)
        try:
            self.assertGreater(local.revision, revision)
            self.assertEqual(local.db.execute('SELECT count(*) FROM resolutions').fetchone()[0], 1)
            with self.assertRaises(JumpError):
                local.available('dev.source-snapshot@1.1.1')
        finally:
            local.close()

    def test_path_escape_rejected(self):
        """Reject a runtime directory redirected outside the workspace by a symlink."""
        outside = self.root / 'outside'
        outside.mkdir()
        (self.work / '.jumpai').symlink_to(outside)
        with self.assertRaises(JumpError):
            open_workspace(self.library, self.work)

    def test_invalid_library_returns_json_error_without_creating_runtime(self):
        """Reject malformed exports at the CLI boundary before creating local state."""
        valid = json.loads(self.library.read_text())
        invalid = [
            '{',
            '[]',
            json.dumps({**valid, 'revision': -1}),
            json.dumps({**valid, 'versions': [{}]}),
            json.dumps({**valid, 'versions': [valid['versions'][0]] * 2}),
        ]
        for raw in invalid:
            with self.subTest(raw=raw[:60]):
                self.library.write_text(raw)
                result = subprocess.run(
                    [sys.executable, str(LAUNCHER), '--library', str(self.library),
                     '--workspace', str(self.work), 'stats'],
                    cwd=self.root, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn('error', json.loads(result.stderr))
                self.assertNotIn('Traceback', result.stderr)
                self.assertFalse((self.work / '.jumpai').exists())

    def test_handoff_rejects_invalid_test_values(self):
        """Reject falsey nonarrays and blank IDs instead of silently omitting checks."""
        catalog = open_workspace(self.library, self.work)
        self.addCleanup(catalog.close)
        for tests in (None, {}, False, 0, '', [' ']):
            with self.subTest(tests=tests), self.assertRaises(JumpError):
                handoff(catalog, self.library, self.work, tests, None, True)

        self.assertEqual(handoff(catalog, self.library, self.work, [], None, True)['commands'], {})

    def test_catalog_closes_connection_when_initialization_fails(self):
        """Release SQLite resources when a setup operation fails after connection."""
        with patch('jumpai.catalog.sqlite3.connect') as connect:
            connection = connect.return_value
            connection.execute.side_effect = sqlite3.OperationalError('setup failed')
            with self.assertRaises(sqlite3.OperationalError):
                Catalog(self.root / 'failed-catalog')
            connection.close.assert_called_once_with()
