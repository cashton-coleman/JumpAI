"""Project specified fields from each record, rejecting missing fields."""

import json
import sys

# Project requested fields; a missing key fails instead of silently dropping data.
args = json.load(sys.stdin)
json.dump({"records": [{field: row[field] for field in args["fields"]}
                       for row in args["records"]]}, sys.stdout, allow_nan=False)
