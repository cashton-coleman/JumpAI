"""Capture real test outcomes; no synthetic timings enter the benchmark dataset."""

import argparse
import json
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class RecordingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        """Initialize unittest reporting plus per-test timing and outcome records."""
        super().__init__(*args, **kwargs)
        self.records = []
        self.statuses = {}

    def startTest(self, test):
        """Start timing and initialize the test outcome before notifying unittest."""
        self.started = time.perf_counter()
        self.statuses[test.id()] = 'passed'
        super().startTest(test)

    def stopTest(self, test):
        """Record elapsed time and final status, then notify unittest that the test stopped."""
        self.records.append({'test': test.id(), 'status': self.statuses[test.id()],
                             'duration_ms': round((time.perf_counter() - self.started) * 1000, 3)})
        super().stopTest(test)

    def addFailure(self, test, err):
        """Record an assertion failure and preserve unittest failure reporting."""
        self.statuses[test.id()] = 'failed'
        super().addFailure(test, err)

    def addError(self, test, err):
        """Record an execution error and preserve unittest error reporting."""
        self.statuses[test.id()] = 'error'
        super().addError(test, err)

    def addSkip(self, test, reason):
        """Record a skipped test and preserve its unittest reason."""
        self.statuses[test.id()] = 'skipped'
        super().addSkip(test, reason)

    def addExpectedFailure(self, test, err):
        """Record an expected failure and preserve unittest reporting."""
        self.statuses[test.id()] = 'expected_failure'
        super().addExpectedFailure(test, err)

    def addUnexpectedSuccess(self, test):
        """Record an unexpected success so it is visible in benchmark outcomes."""
        self.statuses[test.id()] = 'unexpected_success'
        super().addUnexpectedSuccess(test)

    def addSubTest(self, test, subtest, err):
        """Record failing subtest status while preserving unittest subtest reporting."""
        if err is not None:
            self.statuses[test.id()] = 'failed' if issubclass(err[0], test.failureException) else 'error'
        super().addSubTest(test, subtest, err)


def main():
    """Run the selected test module, save measured outcomes, and return its exit status."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_jumpai.py')
    result = unittest.TextTestRunner(verbosity=1, resultclass=RecordingResult).run(suite)
    Path(args.output).write_text(json.dumps(result.records, indent=2) + '\n')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
