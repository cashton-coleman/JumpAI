"""Check verbatim evidence, bounded packets, and workspace path boundaries."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'programs/triage-evidence/evidence.py'
spec = importlib.util.spec_from_file_location('checkpoint_evidence', SOURCE)
evidence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evidence)


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        """Create isolated fixture files and resources used by this test case."""
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'tests').mkdir()
        self.source = 'def transform(value):\n    return value.strip()\n'
        (self.root / 'sample.py').write_text(self.source)
        (self.root / 'tests/test_sample.py').write_text('class Wrong:\n    def test_value(self):\n        wrong()\nclass Right:\n    def test_value(self):\n        self.assertEqual(transform(" A "), "a")\n')
        self.data = {'root': str(self.root), 'path': 'sample.py', 'checks': {'evidence': [
            {'test': 'test_sample.Right.test_value', 'traceback': f'  File "{self.root}/sample.py", line 2, in transform\n  File "/library/mock.py", line 99, in mock\nAssertionError: mismatch'}]}}

    def tearDown(self):
        """Release the test resources and remove temporary fixture files."""
        self.temp.cleanup()

    def test_whole_verbatim_definitions_and_exact_test_identity(self):
        """Verify whole verbatim definitions and exact test identity."""
        result = evidence.collect(self.data)
        self.assertEqual(result['source'], [{'symbol': 'transform', 'line': 1, 'source': self.source.rstrip()}])
        self.assertNotIn('wrong()', result['test_source'][0]['source'])
        self.assertTrue(result['test_source'][0]['source'].endswith('self.assertEqual(transform(" A "), "a")'))
        self.assertEqual(result['failures'][0]['frames'], [{'path': 'sample.py', 'line': 2, 'function': 'transform'}])
        self.assertEqual(result['omitted'], {'failures': [], 'tests': [], 'symbols': []})

    def test_budget_omits_whole_blocks_and_preserves_full_evidence_file(self):
        """Verify budget omits whole blocks and preserves full evidence file."""
        long_source = 'def transform(value):\n    text = "' + 'x' * 2000 + '"\n    return value\n'
        (self.root / 'sample.py').write_text(long_source)
        result = evidence.collect(dict(self.data, max_bytes=1024))
        self.assertLessEqual(len(evidence.encoded(result).encode()), 1024)
        self.assertEqual(result['source'], [])
        self.assertIn('transform', result['omitted']['symbols'])
        full = json.loads(Path(result['evidence_ref']).read_text())
        self.assertEqual(full['source'][0]['source'], long_source.rstrip())

    def test_same_named_methods_are_not_overwritten(self):
        """Verify same named methods are not overwritten."""
        source = 'class A:\n    def transform(self, value):\n        return value\nclass B:\n    def transform(self, value):\n        return value.strip()\n'
        (self.root / 'sample.py').write_text(source)
        result = evidence.collect(self.data)
        self.assertEqual({x['symbol'] for x in result['source']}, {'A.transform', 'B.transform'})

    def test_budget_prioritizes_traceback_function_over_incidental_method(self):
        """Verify budget prioritizes traceback function over incidental method."""
        source = 'def transform(value):\n    note = "' + 't' * 1000 + '"\n    return value\n'
        source += 'def stats():\n    note = "' + 's' * 900 + '"\n    return 0\n'
        (self.root / 'sample.py').write_text(source)
        (self.root / 'tests/test_sample.py').write_text('class Right:\n    def test_value(self):\n        transform("a")\n        stats()\n')
        result = evidence.collect(dict(self.data, max_bytes=2000))
        self.assertIn('transform', [x['symbol'] for x in result['source']])
        self.assertIn('stats', result['omitted']['symbols'])
        self.assertLessEqual(len(evidence.encoded(result).encode()), 2000)

    def test_decorators_and_relative_traceback_locations_are_preserved(self):
        """Verify decorators and relative traceback locations are preserved."""
        source = '@cached\ndef transform(value):\n    return value.strip()\n'
        (self.root / 'sample.py').write_text(source)
        data = dict(self.data, checks={'evidence': [{'test': 'test_sample.Right.test_value',
             'traceback': '  File "sample.py", line 3, in transform\nAssertionError: mismatch'}]})
        result = evidence.collect(data)
        self.assertEqual(result['source'][0]['source'], source.rstrip())
        self.assertEqual(result['source'][0]['line'], 1)
        self.assertEqual(result['failures'][0]['frames'][0]['path'], 'sample.py')

    def test_path_and_artifact_symlink_cannot_escape_workspace(self):
        """Verify path and artifact symlink cannot escape workspace."""
        with self.assertRaisesRegex(ValueError, 'escapes'):
            evidence.collect(dict(self.data, path='../outside.py'))
        outside = self.root.parent / (self.root.name + '-not-written.json')
        (self.root / '.triage-evidence.json').symlink_to(outside)
        with self.assertRaisesRegex(ValueError, 'escapes'):
            evidence.collect(self.data)
        self.assertFalse(outside.exists())
