"""Load an array of JSON objects from a workspace file."""

import json
from pathlib import Path
import sys

# Read the requested file and reject non-object records before returning it.
args = json.load(sys.stdin)
records = json.loads(Path(args["path"]).read_text())
if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
    raise ValueError("Expected a JSON array of objects")

json.dump({"records": records}, sys.stdout, allow_nan=False)
