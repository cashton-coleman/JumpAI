"""Check managed installation, startup guidance, and preservation of user files."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from jumpai.catalog import Catalog
from jumpai.install import BEGIN, END, REPOSITORY, STANDARDS, manage
from jumpai.schema import JumpError


class InstallationTests(unittest.TestCase):
    def setUp(self):
        """Create isolated fixture files and resources used by this test case."""
        self.temp = tempfile.TemporaryDirectory(prefix='jumpai-install-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.profile = self.root / 'profile'
        self.profile.mkdir()
        self.instructions = self.profile / 'AGENTS.md'
        self.instructions.write_text('Keep the user conventions.\n')
        self.args = argparse.Namespace(action='install', host='codex',
            skills_dir=str(self.root / 'skills'), instructions=None,
            codex_home=str(self.profile), catalog=str(self.root / 'catalog'))

    def test_install_repeat_new_process_and_uninstall_preserve_user_data(self):
        """Verify install repeat new process and uninstall preserve user data."""
        result = manage(self.args)
        self.assertTrue(result['healthy'])
        self.assertIn(STANDARDS.read_text().strip(), self.instructions.read_text())
        self.assertIn(STANDARDS.read_text().strip(), result['agent_handoff'])
        self.assertEqual(len(result['targets']), 7)
        catalog = Catalog(result['catalog'])
        try:
            self.assertEqual(catalog.execution_settings(), result['execution'])
            published = json.loads((catalog.home / 'library.json').read_text())
            self.assertEqual(published['execution'], result['execution'])
            catalog.remember('inspect source', 'dev.source-snapshot@1.1.1', self.root, '')
            revision = catalog.revision
        finally:
            catalog.close()
        self.assertTrue(manage(self.args)['healthy'])
        catalog = Catalog(result['catalog'])
        try:
            self.assertEqual(catalog.revision, revision)
            self.assertTrue(catalog.resolve('inspect source', self.root, '')['cache_hit'])
        finally:
            catalog.close()
        text = self.instructions.read_text()
        self.assertEqual(text.count(BEGIN), 1)
        self.instructions.write_text(text + '\nA later user rule.\n')
        child = subprocess.run([sys.executable, str(Path(result['skill']) / 'scripts/install.py'),
            'doctor', '--skills-dir', self.args.skills_dir, '--codex-home', self.args.codex_home],
            cwd=self.root, text=True, capture_output=True)
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertTrue(json.loads(child.stdout)['healthy'])
        self.args.action = 'uninstall'
        self.assertTrue(manage(self.args)['uninstalled'])
        self.assertEqual(self.instructions.read_text(), 'Keep the user conventions.\n\nA later user rule.\n')
        self.assertTrue((self.root / 'catalog/catalog.sqlite3').is_file())
        self.assertFalse((self.root / 'skills/jumpai').exists())

    def test_override_is_detected_and_reinstall_moves_only_managed_block(self):
        """Verify override is detected and reinstall moves only managed block."""
        manage(self.args)
        override = self.profile / 'AGENTS.override.md'
        override.write_text('Temporary user preference.\n')
        self.args.action = 'doctor'
        result = manage(self.args)
        self.assertFalse(result['healthy'])
        self.assertTrue(any('shadowed' in issue for issue in result['issues']))
        self.args.action = 'install'
        result = manage(self.args)
        self.assertTrue(result['healthy'])
        self.assertEqual(result['instructions'], str(override))
        self.assertEqual(self.instructions.read_text(), 'Keep the user conventions.\n')
        self.assertTrue(override.read_text().startswith('Temporary user preference.\n'))
        self.assertIn(STANDARDS.read_text().strip(), override.read_text())

    def test_conflict_and_bad_markers_do_not_create_catalog_or_replace_skill(self):
        """Verify conflict and bad markers do not create catalog or replace skill."""
        skill = self.root / 'skills/jumpai'
        skill.mkdir(parents=True)
        (skill / 'mine').write_text('preserve')
        with self.assertRaises(JumpError):
            manage(self.args)
        self.assertEqual((skill / 'mine').read_text(), 'preserve')
        self.assertFalse((self.root / 'catalog').exists())
        self.args.skills_dir = str(self.root / 'other-skills')
        self.instructions.write_text('user\n' + BEGIN + 'unfinished')
        with self.assertRaises(JumpError):
            manage(self.args)
        self.assertFalse((self.root / 'catalog').exists())

    def test_missing_instruction_or_edited_block_detected(self):
        """Verify missing instruction or edited block detected."""
        manage(self.args)
        self.instructions.write_text(self.instructions.read_text().replace('Use JumpAI', 'Changed JumpAI'))
        self.args.action = 'doctor'
        self.assertFalse(manage(self.args)['healthy'])
        self.args.action = 'uninstall'
        with self.assertRaises(JumpError):
            manage(self.args)
        self.assertTrue((self.root / 'skills/jumpai').is_symlink())

    def test_generic_host_requires_explicit_discovery_paths(self):
        """Verify generic host requires explicit discovery paths."""
        self.args.host = 'generic'
        with self.assertRaises(JumpError):
            manage(self.args)
        self.args.instructions = str(self.root / 'HOST.md')
        self.assertTrue(manage(self.args)['healthy'])
        self.assertIn(STANDARDS.read_text().strip(), Path(self.args.instructions).read_text())

    def test_outdated_standards_are_detected_and_refreshed(self):
        """Simulate an older managed block; doctor flags it and reinstall restores standards."""
        manage(self.args)
        receipt = self.root / 'skills/.jumpai-install.json'
        state = json.loads(receipt.read_text())
        old = state['block'].replace(STANDARDS.read_text().strip(), '')
        self.instructions.write_text(self.instructions.read_text().replace(state['block'], old))
        state['block'] = old
        receipt.write_text(json.dumps(state))

        self.args.action = 'doctor'
        result = manage(self.args)
        self.assertFalse(result['healthy'])
        self.assertTrue(any('standards' in issue for issue in result['issues']))

        self.args.action = 'install'
        self.assertTrue(manage(self.args)['healthy'])
        self.assertIn(STANDARDS.read_text().strip(), self.instructions.read_text())

    def test_instruction_symlink_is_preserved(self):
        """Verify instruction symlink is preserved."""
        target = self.root / 'shared-instructions.md'
        self.instructions.rename(target)
        self.instructions.symlink_to(target)
        self.assertTrue(manage(self.args)['healthy'])
        self.assertTrue(self.instructions.is_symlink())
        self.assertIn(BEGIN, target.read_text())

    def test_doctor_reports_disabled_skill_and_instruction_truncation(self):
        """Verify doctor reports disabled skill and instruction truncation."""
        result = manage(self.args)
        (self.profile / 'config.toml').write_text('project_doc_max_bytes = 10\n[[skills.config]]\npath = ' + json.dumps(str(Path(result['skill']) / 'SKILL.md')) + '\nenabled = false\n')
        self.args.action = 'doctor'
        result = manage(self.args)
        self.assertFalse(result['healthy'])
        self.assertTrue(any('disabled' in issue for issue in result['issues']))
        self.assertTrue(any('byte limit' in issue for issue in result['issues']))
