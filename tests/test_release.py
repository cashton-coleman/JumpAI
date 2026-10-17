"""Exercise the distributed shell installer and a saved workflow outside the live checkout."""

import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tarfile
import tempfile
import unittest


class ReleaseTests(unittest.TestCase):
    """Keep release validation small, isolated, and independent of real user settings."""

    def test_clean_package_install_recipe_and_uninstall(self):
        """Build/extract a release, verify standards and a recipe, then preserve user data on removal."""
        repository = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='jumpai-clean-release-') as temp:
            root = Path(temp).resolve()
            environment = dict(os.environ)
            for key in ('PYTHONPATH', 'PYTHONHOME', 'JUMPAI_HOME', 'CODEX_HOME'):
                environment.pop(key, None)
            environment['JUMPAI_PYTHON'] = sys.executable

            def run(command, cwd=root, input_text=None):
                """Execute one isolated command, requiring successful JSON output within 20 seconds."""
                result = subprocess.run(command, cwd=cwd, env=environment, text=True,
                                        capture_output=True, timeout=20, input=input_text)
                self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
                return json.loads(result.stdout)

            # Build from allowed sources and extract into a new directory with spaces.
            built = run([sys.executable, str(repository / 'scripts/package_release.py'), '--output', str(root / 'dist')])
            archive = Path(built['archive'])
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), built['sha256'])
            extracted = root / 'clean package'
            extracted.mkdir()
            with tarfile.open(archive) as bundle:
                members = bundle.getmembers()
                for member in members:
                    self.assertTrue(member.isfile())
                    self.assertFalse({'__pycache__', '.git', '.jumpai', 'results', 'agent-results'}.intersection(Path(member.name).parts))
                bundle.extractall(extracted, filter='data')
            package = next(extracted.iterdir())
            standards = (package / 'skills/jumpai/AGENTS.override.md').read_text()
            self.assertEqual((package / 'AGENTS.override.md').read_text(), standards)
            self.assertTrue((package / 'programs/native-review/LICENSE.MIT').is_file())

            # Route the bootstrap's GitHub clone to a real local repository for offline testing.
            environment['GIT_CONFIG_GLOBAL'] = os.devnull
            environment['GIT_CONFIG_NOSYSTEM'] = '1'
            for arguments in (['init', '-b', 'main'], ['add', '.'],
                              ['-c', 'user.name=Release Test', '-c', 'user.email=release@example.invalid',
                               '-c', 'commit.gpgsign=false', '-c', 'core.hooksPath=/dev/null',
                               'commit', '-m', 'Release fixture']):
                result = subprocess.run(['git', *arguments], cwd=package, env=environment,
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
            environment.update({
                'GIT_CONFIG_COUNT': '1',
                'GIT_CONFIG_KEY_0': f'url.{package.as_uri()}.insteadOf',
                'GIT_CONFIG_VALUE_0': 'https://github.com/cashton-coleman/JumpAI.git',
                'JUMPAI_INSTALL_DIR': str(root / 'installed checkout'),
            })
            bootstrap = (package / 'install-from-github.sh').read_text()

            # Install from piped shell input, preserving existing host instructions.
            profile = root / 'profile'
            profile.mkdir()
            instructions = profile / 'AGENTS.override.md'
            instructions.write_text('Keep my preference.\n')
            flags = ['--codex-home', str(profile), '--skills-dir', str(root / 'skills'), '--catalog', str(root / 'catalog')]
            installed = run(['sh', '-s', '--', 'install', *flags], input_text=bootstrap)
            package = Path(environment['JUMPAI_INSTALL_DIR'])
            shell = str(package / 'install.sh')
            self.assertTrue(installed['healthy'])
            self.assertEqual(Path(installed['skill']).resolve(), package / 'skills/jumpai')
            self.assertIn(standards.strip(), instructions.read_text())
            self.assertTrue(run([shell, 'doctor', *flags])['healthy'])

            # A repeated bootstrap must refuse to replace the existing installation.
            repeated = subprocess.run(['sh'], input=bootstrap, cwd=root, env=environment,
                                      text=True, capture_output=True, timeout=20)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertIn('Destination already exists', repeated.stderr)

            # Execute and replay a bundled chained workflow using only the extracted runtime.
            workspace = root / 'task'
            workspace.mkdir()
            (workspace / 'sample.py').write_text('def normalize(value):\n    """Strip surrounding whitespace."""\n    return value.strip()\n')
            (workspace / 'test_sample.py').write_text(
                '"""Validate the release smoke fixture."""\nimport unittest\nfrom sample import normalize\n\n'
                'class Check(unittest.TestCase):\n    """Check normalization behavior."""\n\n'
                '    def test_normalize(self):\n        """Whitespace should be removed."""\n'
                '        self.assertEqual(normalize(" a "), "a")\n')
            launcher = Path(installed['skill']) / 'scripts/jumpai.py'
            # Verify the release ships the usage reader without depending on host telemetry.
            empty_session = root / 'empty-session.jsonl'
            empty_session.write_text('')
            usage = run([sys.executable, str(launcher), 'usage', '--session', str(empty_session)])
            self.assertIsNone(usage['previous'])
            self.assertIsNone(usage['jumpai_average'])
            request = {'root': str(workspace), 'source_path': 'sample.py', 'tests': ['test_sample.Check.test_normalize']}
            receipt = run([sys.executable, str(launcher), '--library', str(root / 'catalog/library.json'),
                           '--workspace', str(workspace), 'run', '--target', 'repo.regression-triage@1.4.0',
                           '--resumable', '--input', json.dumps(request), '--save-recipe', 'release-smoke'], workspace)
            self.assertEqual(receipt['status'], 'completed')
            self.assertTrue(receipt['jumpai_execution'])
            replay = run([*shlex.split(receipt['recipe']['command']), '--resumable'], workspace)
            self.assertEqual(replay['status'], 'completed')
            self.assertTrue(replay['jumpai_execution'])

            # Uninstall removes managed discovery and guidance, preserving user data.
            self.assertTrue(run([shell, 'uninstall', *flags])['uninstalled'])
            self.assertEqual(instructions.read_text(), 'Keep my preference.\n')
            self.assertFalse(Path(installed['skill']).exists())
            self.assertTrue((root / 'catalog/library.json').is_file())
