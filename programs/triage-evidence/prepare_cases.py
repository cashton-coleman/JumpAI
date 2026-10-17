"""Prepare small independent verification fixtures for the evidence contract."""

import hashlib
import json
from pathlib import Path

root = Path('/private/tmp/jumpai-evidence-verification')
root.mkdir(exist_ok=True)
(root / 'tests').mkdir(exist_ok=True)
source = 'def normalize(value):\n    return value.strip()\n'
test = 'class Example:\n    def test_value(self):\n        self.assertEqual(normalize(" A "), "a")\n'
(root / 'sample.py').write_text(source)
(root / 'tests/test_sample.py').write_text(test)
identifier = 'test_sample.Example.test_value'
trace = f'Traceback:\n  File "{root}/sample.py", line 2, in normalize\nAssertionError: mismatched'
base = {'path': 'sample.py', 'sha256': hashlib.sha256(source.encode()).hexdigest(), 'evidence_ref': str(root / '.triage-evidence.json'),
        'excerpt_policy': 'Verbatim complete definitions, including decorators; enclosing class/module context is not included.',
        'failures': [], 'test_source': [], 'source': [], 'omitted': {'failures': [], 'tests': [], 'symbols': []}}
failed = dict(base, failures=[{'test': identifier, 'error': 'AssertionError: mismatched', 'frames': [{'path': 'sample.py', 'line': 2, 'function': 'normalize'}]}],
              test_source=[{'test': identifier, 'source': 'def test_value(self):\n        self.assertEqual(normalize(" A "), "a")'}],
              source=[{'symbol': 'normalize', 'line': 1, 'source': source.rstrip()}])
cases = [{'input': {'root': str(root), 'path': 'sample.py', 'checks': {'evidence': []}}, 'output': base},
         {'input': {'root': str(root), 'path': 'sample.py', 'checks': {'evidence': [{'test': identifier, 'traceback': trace}]}}, 'output': failed}]
(Path(__file__).parent / 'cases.json').write_text(json.dumps(cases, indent=2) + '\n')
