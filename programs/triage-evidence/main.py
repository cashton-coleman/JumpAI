"""Read a JSON diagnosis request and emit bounded source evidence."""

import json
import sys
from evidence import collect
print(json.dumps(collect(json.load(sys.stdin))))
