"""Check turn accounting with deterministic logs, without reading real conversation data."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from jumpai.usage import execution_receipt, footer, report, session_path


class UsageTests(unittest.TestCase):
    """Exercise whole-turn deltas, receipt detection, incomplete logs, and the shipped CLI."""

    def setUp(self):
        """Allocate a disposable session log and an isolated catalog destination."""
        temporary = tempfile.TemporaryDirectory(prefix='jumpai-usage-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.session = self.root / 'session.jsonl'
        self.events = []

    def event(self, second, kind, **fields):
        """Append a timestamped event with explicit test data, without writing until requested."""
        self.events.append({'timestamp': f'2026-10-17T00:00:{second:02d}+00:00',
                            'type': 'event_msg', 'payload': {'type': kind, **fields}})

    def counts(self, second, incoming, cached, outgoing):
        """Append cumulative input/output telemetry; cached input is already included."""
        self.event(second, 'token_count', info={'total_token_usage': {
            'input_tokens': incoming, 'cached_input_tokens': cached,
            'output_tokens': outgoing, 'total_tokens': incoming + outgoing}})

    def receipt(self, value):
        """Wrap a result as Codex's nested tool output instead of a user/source message."""
        output = [{'type': 'input_text', 'text': json.dumps({'output': json.dumps(value)})}]
        self.events.append({'type': 'response_item',
                            'payload': {'type': 'custom_tool_call_output', 'output': output}})

    def save(self, tail=''):
        """Write complete fixture records followed by an optional incomplete append."""
        self.session.write_text(''.join(json.dumps(e) + '\n' for e in self.events) + tail)

    def test_previous_and_assisted_means_ignore_duplicates_and_active_turn(self):
        """Measure all completed turns but average only those with execution receipts."""
        self.counts(0, 100, 50, 10)
        self.event(1, 'task_started', turn_id='a')
        self.receipt({'run_id': 1, 'id': 'checks@1.0.0', 'duration_ms': 1, 'output': {}})
        self.counts(2, 200, 100, 20)
        self.counts(2, 200, 100, 20)
        self.event(3, 'task_complete', turn_id='a')
        self.event(4, 'task_started', turn_id='b')
        self.counts(5, 500, 300, 50)
        self.event(7, 'task_complete', turn_id='b')
        self.event(8, 'task_started', turn_id='c')
        self.receipt({'jumpai_execution': True, 'session_id': 'workflow'})
        self.counts(9, 700, 400, 70)
        self.event(12, 'task_complete', turn_id='c')
        self.event(13, 'task_started', turn_id='active')
        self.counts(14, 900, 500, 90)
        self.save('{"type":')

        result = report(self.session)
        self.assertEqual(result['previous']['turn_id'], 'c')
        self.assertEqual(result['previous']['usage']['total_tokens'], 220)
        self.assertEqual(result['jumpai_average'], {'turns': 2, 'wall_seconds': 3.0, 'total_tokens': 165.0})
        self.assertIn('in 200 (100 cached) · out 20 · total 220', footer(result))

    def test_missing_baseline_reset_and_abort_do_not_fabricate_usage(self):
        """Exclude missing/reset counters from means and interrupted work from completed turns."""
        self.event(1, 'task_started', turn_id='no-baseline')
        self.receipt({'jumpai_execution': True})
        self.counts(2, 100, 50, 10)
        self.event(3, 'task_complete', turn_id='no-baseline')
        self.event(4, 'task_started', turn_id='reset')
        self.receipt({'jumpai_execution': True})
        self.counts(5, 20, 10, 2)
        self.event(6, 'task_complete', turn_id='reset')
        self.event(7, 'task_started', turn_id='aborted')
        self.event(8, 'turn_aborted')
        self.event(9, 'task_complete', turn_id='aborted')
        self.save()

        result = report(self.session)
        self.assertEqual(result['previous']['turn_id'], 'reset')
        self.assertIsNone(result['previous']['usage'])
        self.assertIsNone(result['jumpai_average'])
        self.assertEqual(result['jumpai_unmeasured_turns'], 2)
        self.assertIn('tokens unavailable', footer(result))

    def test_only_returned_receipts_count_and_bad_logs_are_unavailable(self):
        """Reject mere mentions, source examples, invalid counters, and corrupt complete records."""
        self.assertFalse(execution_receipt({'source': '{"jumpai_execution":true}'}))
        self.assertFalse(execution_receipt('jumpai run --target checks@1.0.0'))
        self.counts(0, 100, 50, 10)
        self.event(1, 'task_started', turn_id='invalid')
        self.counts(2, 200, 250, 20)
        self.event(3, 'task_complete', turn_id='invalid')
        self.save()
        self.assertIsNone(report(self.session)['previous']['usage'])
        for invalid in ('broken\n', '[]\n'):
            with self.subTest(invalid=invalid):
                self.session.write_text(invalid)
                self.assertIn('unavailable', report(self.session))
        self.assertIn('unavailable', report(self.root / 'missing.jsonl'))

    def test_cli_and_thread_selection_do_not_create_catalog(self):
        """Resolve one exact thread and run the packaged CLI without catalog writes."""
        self.counts(0, 0, 0, 0)
        self.event(1, 'task_started', turn_id='previous')
        self.counts(2, 10, 5, 1)
        self.event(3, 'task_complete', turn_id='previous')
        self.save()
        identity = '00000000-0000-4000-8000-000000000001'
        directory = self.root / 'sessions/2026/10/17'
        directory.mkdir(parents=True)
        selected = directory / f'rollout-test-{identity}.jsonl'
        selected.write_text(self.session.read_text())
        self.assertEqual(session_path(thread_id=identity, sessions_dir=self.root / 'sessions'), selected)
        self.assertIn('unavailable', report(thread_id='../bad', sessions_dir=self.root))
        catalog = self.root / 'must-not-exist'
        launcher = Path(__file__).resolve().parents[1] / 'skills/jumpai/scripts/jumpai.py'
        process = subprocess.run([sys.executable, str(launcher), '--home', str(catalog), 'usage',
                                  '--session', str(selected), '--footer'],
                                 capture_output=True, text=True, timeout=10, cwd=self.root)
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn('Usage (previous): 2.0s · in 10 (5 cached) · out 1 · total 11', process.stdout)
        self.assertFalse(catalog.exists())
