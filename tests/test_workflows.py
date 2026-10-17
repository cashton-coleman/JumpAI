"""Focused state-machine and CLI integration tests for resumable workflows."""

import contextlib
from concurrent.futures import ThreadPoolExecutor
import io
import json
from pathlib import Path
import tempfile
import unittest

from jumpai.catalog import Catalog
from jumpai.cli import main
from jumpai.runner import Runner
from jumpai.schema import JumpError
from jumpai.workflow import WorkflowSessions

VALUE = {'type': 'object', 'properties': {'value': {'type': 'integer', 'minimum': 0}}, 'required': ['value'], 'additionalProperties': False}
INPUT = {'type': 'object', 'properties': {'value': {'type': 'integer'}, 'needs': {'type': 'boolean'}}, 'required': ['value', 'needs'], 'additionalProperties': False}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        """Create isolated fixture files and resources used by this test case."""
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.catalog = Catalog(self.root / 'catalog')
        self.runner = Runner(self.catalog, self.root)
        self.sessions = WorkflowSessions(self.runner)
        self.counter = self.root / 'calls.txt'
        (self.root / 'main.py').write_text('import json,sys\nfrom pathlib import Path\nd=json.load(sys.stdin)\np=Path("calls.txt")\np.write_text((p.read_text() if p.exists() else "")+"x")\nif d["value"]==99: raise RuntimeError("intentional")\nprint(json.dumps(d))\n')
        p = {'name': 'count', 'version': '1', 'purpose': 'Count executions', 'runtime': 'python', 'entrypoint': 'main.py', 'files': ['main.py'], 'input_schema': VALUE, 'output_schema': VALUE}
        self.register('program.json', p)
        self.catalog.set_validated('count@1', True)
        self.manifest = {'name': 'flow', 'version': '1', 'kind': 'workflow', 'purpose': 'Conditional decision fixture', 'input_schema': INPUT, 'output_schema': VALUE,
            'steps': [
                {'name': 'before', 'target': 'count@1', 'bindings': {'value': {'input': '/value'}}},
                {'name': 'choose', 'when': {'value': {'input': '/needs'}, 'equals': True}, 'otherwise': {'step': 'before', 'pointer': ''},
                 'decision': {'prompt': 'Choose a value', 'schema': VALUE, 'context': {'previous': {'step': 'before', 'pointer': '/value'}}}},
                {'name': 'after', 'target': 'count@1', 'bindings': {'value': {'step': 'choose', 'pointer': '/value'}}}],
            'output': {'step': 'after', 'pointer': ''}, 'summary': {'step': 'after', 'pointer': ''}}
        self.register('workflow.json', self.manifest)
        self.catalog.set_validated('flow@1', True)

    def tearDown(self):
        """Release the test resources and remove temporary fixture files."""
        self.catalog.close()
        self.temp.cleanup()

    def register(self, name, manifest):
        """Write a fixture manifest and return the catalog registration result."""
        path = self.root / name
        path.write_text(json.dumps(manifest))
        return self.catalog.register(path)

    def start(self):
        """Start the fixture workflow and return its initial decision receipt."""
        return self.sessions.start('flow@1', {'needs': True, 'value': 1})

    def cli(self, *args):
        """Capture CLI output and return the exit status with its decoded JSON response."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            status = main(['--home', str(self.catalog.home), *args, '--cwd', str(self.root)])
        return (status, json.loads(output.getvalue()))

    def test_restart_resume_keeps_prior_work_and_returns_compact_summary(self):
        """Verify restart resume keeps prior work and returns compact summary."""
        paused = self.start()
        self.assertEqual(paused['status'], 'needs_decision')
        self.assertEqual(paused['decision']['context'], {'previous': 1})
        self.assertNotIn('results', paused)
        self.catalog.close()
        self.catalog = Catalog(self.root / 'catalog')
        self.sessions = WorkflowSessions(Runner(self.catalog, self.root))
        done = self.sessions.resume(paused['session_id'], {'value': 2}, step='choose')
        self.assertEqual(done['summary'], {'value': 2})
        self.assertEqual(self.counter.read_text(), 'xx')
        self.assertEqual(self.sessions.result(done['session_id'])['output'], {'value': 2})
        self.assertEqual(self.sessions.result(done['session_id'], step='before')['output'], {'value': 1})

    def test_false_condition_skips_decision_and_uses_explicit_fallback(self):
        """Verify false condition skips decision and uses explicit fallback."""
        done = self.sessions.start('flow@1', {'needs': False, 'value': 3})
        self.assertEqual(done['status'], 'completed')
        self.assertEqual(done['summary'], {'value': 3})
        self.assertEqual(done['steps_skipped'], 1)
        self.assertEqual(self.counter.read_text(), 'xx')

    def test_invalid_stale_wrong_scope_and_disabled_resume_do_not_advance(self):
        """Verify invalid stale wrong scope and disabled resume do not advance."""
        sid = self.start()['session_id']
        for decision, step in [({'value': -1}, 'choose'), ({'value': 1}, 'old-checkpoint')]:
            with self.assertRaises(JumpError):
                self.sessions.resume(sid, decision, step=step)
        other = self.root / 'other'
        other.mkdir()
        with self.assertRaisesRegex(JumpError, 'workspace'):
            WorkflowSessions(Runner(self.catalog, other)).resume(sid, {'value': 1}, step='choose')
        self.catalog.set_enabled('count@1', False)
        with self.assertRaisesRegex(JumpError, 'disabled'):
            self.sessions.resume(sid, {'value': 1}, step='choose')
        self.assertEqual(self.sessions.status(sid)['status'], 'needs_decision')
        self.assertEqual(self.counter.read_text(), 'x')

    def test_concurrent_or_duplicate_resume_executes_continuation_once(self):
        """Verify concurrent or duplicate resume executes continuation once."""
        sid = self.start()['session_id']
        def resume():
            """Attempt a continuation through a separate connection; return completion or rejection."""
            c = Catalog(self.catalog.home)
            try:
                try:
                    return WorkflowSessions(Runner(c, self.root)).resume(sid, {'value': 2}, step='choose')['status']
                except JumpError:
                    return 'rejected'
            finally:
                c.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: resume(), range(2)))
        self.assertCountEqual(results, ['completed', 'rejected'])
        self.assertEqual(self.counter.read_text(), 'xx')

    def test_failed_continuation_cannot_be_blindly_replayed(self):
        """Verify failed continuation cannot be blindly replayed."""
        sid = self.start()['session_id']
        with self.assertRaisesRegex(JumpError, sid):
            self.sessions.resume(sid, {'value': 99}, step='choose')
        self.assertEqual(self.sessions.status(sid)['status'], 'failed')
        with self.assertRaisesRegex(JumpError, 'failed'):
            self.sessions.resume(sid, {'value': 2}, step='choose')
        self.assertEqual(self.counter.read_text(), 'xx')

    def test_verification_covers_decisions_and_conditional_programs(self):
        """Verify verification covers decisions and conditional programs."""
        cases = self.root / 'cases.json'
        cases.write_text(json.dumps([
            {'input': {'needs': True, 'value': 1}, 'decisions': {'choose': {'value': 4}}, 'output': {'value': 4}},
            {'input': {'needs': False, 'value': 2}, 'output': {'value': 2}}]))
        self.assertEqual(self.runner.verify('flow@1', cases)['cases_passed'], 2)
        m = json.loads(json.dumps(self.manifest))
        m['version'] = '2'
        m['steps'] = [m['steps'][0]]
        m['steps'][0].update(when={'value': {'input': '/needs'}, 'equals': True}, otherwise={'value': {'value': 8}})
        m['output'] = m['summary'] = {'step': 'before', 'pointer': ''}
        self.register('conditional.json', m)
        self.assertEqual(self.runner.run('flow@2', {'needs': False, 'value': 1}, allow_experimental=True)['output'], {'value': 8})

    def test_cli_resume_saves_recipe_and_replay_starts_new_session(self):
        """Verify cli resume saves recipe and replay starts new session."""
        code, paused = self.cli('run', '--target', 'flow@1', '--input', '{"needs":true,"value":1}', '--resumable')
        self.assertEqual(code, 0)
        sid = paused['session_id']
        code, done = self.cli('resume', sid, '--step', 'choose', '--decision', '{"value":2}', '--save-recipe', 'flow')
        self.assertEqual(code, 0)
        self.assertIn('--resumable', done['recipe']['command'])
        self.assertEqual(self.cli('status', sid)[1]['status'], 'completed')
        self.assertEqual(self.cli('result', sid)[1]['output'], {'value': 2})
        code, replay = self.cli('jump', 'flow', '--resumable')
        self.assertEqual(code, 0)
        self.assertNotEqual(replay['session_id'], sid)
        self.assertEqual(replay['status'], 'needs_decision')
        self.assertEqual(self.counter.read_text(), 'xxx')

    def test_registration_and_context_limits_fail_before_any_continuation(self):
        """Verify registration and context limits fail before any continuation."""
        m = json.loads(json.dumps(self.manifest))
        m['version'] = 'bad'
        m['steps'][0]['when'] = {'value': {'step': 'after', 'pointer': ''}, 'equals': True}
        with self.assertRaisesRegex(JumpError, 'preceding'):
            self.register('bad.json', m)
        m = json.loads(json.dumps(self.manifest))
        m['version'] = 'large'
        m['steps'][1]['decision']['context'] = {'huge': {'value': 'x' * 5000}}
        self.register('large.json', m)
        with self.assertRaisesRegex(JumpError, '4 KiB'):
            self.sessions.start('flow@large', {'needs': True, 'value': 1}, allow_experimental=True)
        self.assertEqual(self.counter.read_text(), 'x')

    def test_cli_saves_on_immediate_completion_but_not_on_pause(self):
        """Verify cli saves on immediate completion but not on pause."""
        code, paused = self.cli('run', '--target', 'flow@1', '--resumable', '--input', '{"needs":true,"value":1}',
                                '--save-recipe', 'later', '--remember', 'later')
        self.assertEqual(code, 0)
        self.assertEqual(paused['status'], 'needs_decision')
        self.assertIn('recipe_warning', paused)
        self.assertEqual(self.catalog.stats()['recipes'], 0)
        self.assertEqual(self.catalog.stats()['resolutions'], 0)
        code, done = self.cli('run', '--target', 'flow@1', '--resumable', '--input', '{"needs":false,"value":1}',
                              '--save-recipe', 'healthy', '--remember', 'healthy')
        self.assertEqual(code, 0)
        self.assertEqual(done['status'], 'completed')
        self.assertIn('--resumable', done['recipe']['command'])
        self.assertEqual(self.catalog.stats()['recipes'], 1)
        self.assertEqual(self.catalog.stats()['resolutions'], 1)
        self.assertEqual(self.cli('jump', 'healthy', '--resumable')[1]['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
