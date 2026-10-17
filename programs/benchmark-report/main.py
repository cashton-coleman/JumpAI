"""Read a JSON reporting request and emit the rendered report locations."""

import json
import sys
from render import render
print(json.dumps(render(json.load(sys.stdin)),separators=(',',':')))
