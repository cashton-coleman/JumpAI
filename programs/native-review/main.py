"""Portable fallback for the native source-review operations; inputs are JSON."""

from collections import Counter
import json
import math
from pathlib import Path
import sys


def sources(args):
    """Yield sorted relative paths and bytes; reject missing files or escapes from root."""
    root = Path(args['root']).resolve(strict=True)
    for name in sorted(args['files']):
        path = (root / name).resolve(strict=True)
        if Path(name).is_absolute() or not path.is_relative_to(root):
            raise ValueError('Source escapes root')
        yield name, path.read_bytes()


def inventory(args):
    """Count bytes and LF-delimited lines, including an unterminated final line."""
    rows = [{'file': name, 'bytes': len(data),
             'lines': data.count(b'\n') + bool(data and not data.endswith(b'\n'))}
            for name, data in sources(args)]
    return {'files_scanned': len(rows), 'bytes': sum(row['bytes'] for row in rows),
            'lines': sum(row['lines'] for row in rows), 'files': rows}


def audit(args):
    """Scan literal markers and whitespace by line; no source parsing is performed."""
    markers, trailing = [], []
    for name, data in sources(args):
        lines = data.split(b'\n')
        if lines[-1] == b'':
            lines.pop()
        for number, line in enumerate(lines, 1):
            line = line.removesuffix(b'\r')
            for marker in ('TODO', 'FIXME'):
                if marker.encode() in line:
                    markers.append({'file': name, 'line': number, 'marker': marker})
            if line.endswith((b' ', b'\t')):
                trailing.append({'file': name, 'line': number})
    return {'files_scanned': len(args['files']), 'markers': markers, 'trailing_whitespace': trailing}


def summarize(args):
    """Validate recorded outcomes and summarize counts, failures, and three slowest tests."""
    records = json.loads(Path(args['path']).read_text())
    allowed = {'passed', 'failed', 'error', 'skipped', 'expected_failure', 'unexpected_success'}
    if not isinstance(records, list):
        raise ValueError('Expected outcome array')
    for row in records:
        if (not isinstance(row, dict) or not isinstance(row.get('test'), str)
                or row.get('status') not in allowed or type(row.get('duration_ms')) not in (int, float)
                or not math.isfinite(row['duration_ms']) or row['duration_ms'] < 0):
            raise ValueError('Invalid outcome record')
    total = sum(row['duration_ms'] for row in records)
    if not math.isfinite(total):
        raise ValueError('Duration total overflow')
    slowest = sorted(records, key=lambda row: (-row['duration_ms'], row['test']))[:3]
    return {'tests': len(records), 'statuses': dict(Counter(row['status'] for row in records)),
            'total_test_ms': math.floor(total * 1000 + 0.5) / 1000,
            'failures': sorted(row['test'] for row in records if row['status'] in {'failed', 'error', 'unexpected_success'}),
            'slowest': [{key: row[key] for key in ('test', 'duration_ms')} for row in slowest]}


def main():
    """Dispatch the fixed manifest argument; errors propagate with a nonzero exit status."""
    args = json.load(sys.stdin)
    operations = {'inventory': inventory, 'audit': audit, 'summarize': summarize, 'combine': lambda value: value}
    json.dump(operations[sys.argv[1]](args), sys.stdout, allow_nan=False)


if __name__ == '__main__':
    main()
