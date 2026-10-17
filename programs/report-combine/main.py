"""Combine independently computed repository checks into one structured report."""

import json
import sys

report = json.load(sys.stdin)
json.dump(report, sys.stdout, sort_keys=True, separators=(",", ":"))
