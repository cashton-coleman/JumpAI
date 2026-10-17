"""Check inline local file links in Markdown; excludes code blocks and remote links."""

import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

# Scan inline links while tracking fences; do not fetch remote URLs.
args = json.load(sys.stdin)
root = Path(args["root"]).resolve()
checked = 0
broken = []
for relative in sorted(args["files"]):
    source = root / relative
    fence = None
    for number, line in enumerate(source.read_text().splitlines(), 1):
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
            continue
        if fence:
            continue
        for match in re.finditer(r"(?<!!)\[[^\]\n]+\]\((<[^>]+>|[^\s)]+)(?:\s+\"[^\"]*\")?\)", line):
            target = match.group(1).strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            path = Path(unquote(parsed.path))
            resolved = path if path.is_absolute() else source.parent / path
            checked += 1
            if not resolved.exists():
                broken.append({"file": relative, "line": number, "target": target})
json.dump({"files_scanned": len(args["files"]), "local_links_checked": checked,
           "broken": broken}, sys.stdout, sort_keys=True, separators=(",", ":"))
