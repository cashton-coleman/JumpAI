"""Summarize recorded unittest outcomes and identify the slowest tests."""

from collections import Counter
import json
from pathlib import Path
import sys

# Validate recorded outcomes before aggregation; timings are never synthesized.
args = json.load(sys.stdin)
records = json.loads(Path(args["path"]).read_text())
if not isinstance(records, list):
    raise ValueError("Expected an array of test outcome records")
for row in records:
    if (not isinstance(row, dict) or not isinstance(row.get("test"), str)
            or row.get("status") not in {"passed", "failed", "error", "skipped", "expected_failure", "unexpected_success"}
            or type(row.get("duration_ms")) not in (int, float)
            or not 0 <= row["duration_ms"] < float("inf")):
        raise ValueError("Invalid test outcome record")

# Return stable ordering for counts, failures, and the slowest recorded tests.
counts = dict(sorted(Counter(row["status"] for row in records).items()))
slowest = sorted(records, key=lambda row: (-row["duration_ms"], row["test"]))[:args.get("top", 3)]
json.dump({"tests": len(records), "statuses": counts,
           "total_test_ms": round(sum(row["duration_ms"] for row in records), 3),
           "failures": sorted(row["test"] for row in records if row["status"] in {"failed", "error", "unexpected_success"}),
           "slowest": [{key: row[key] for key in ("test", "duration_ms")} for row in slowest]},
          sys.stdout, sort_keys=True, separators=(",", ":"))
