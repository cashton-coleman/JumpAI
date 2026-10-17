"""Summarize one numeric field in a collection of records."""

import json
import math
import statistics
import sys

# Validate measurements before calculating summary statistics.
args = json.load(sys.stdin)
values = [row[args["field"]] for row in args["records"]]
if not values or any(type(v) not in (int, float) or not math.isfinite(v) for v in values):
    raise ValueError("Expected a nonempty collection of finite numbers; booleans are not numeric measurements")

json.dump({"count": len(values), "min": min(values), "max": max(values),
           "mean": statistics.mean(values), "median": statistics.median(values)},
          sys.stdout, allow_nan=False)
