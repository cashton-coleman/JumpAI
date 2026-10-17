"""Read scoped source snapshots and complete named Python definitions for editing."""

import ast
import copy
import hashlib
import json
from pathlib import Path
import sys

def first_line(node):
    """Return the first decorator or definition line for a syntax-tree node."""
    return min([node.lineno] + [d.lineno for d in getattr(node, 'decorator_list', [])])


def complete_source(source, node):
    """Return a verbatim definition including decorators without mutating its syntax node."""
    located = copy.copy(node)
    located.lineno = first_line(node)
    return ast.get_source_segment(source, located)


# Read only scoped source files and return complete requested definitions.
request = json.load(sys.stdin)
root = Path(request['root']).resolve()
files = []
for item in request['files']:
    path = (root / item['path']).resolve()
    if not path.is_relative_to(root):
        raise ValueError('source path escapes workspace')
    raw = path.read_bytes()
    source = raw.decode('utf-8')
    result = {'path': item['path'], 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    if item.get('include_text', False):
        result['source'] = source
    symbols = item.get('symbols', [])
    if symbols:
        definitions = {}
        def visit(node, prefix=''):
            """Walk nested definitions while retaining qualified names and their syntax nodes."""
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    name = prefix + child.name
                    definitions[name] = child
                    visit(child, name + '.')
                else:
                    visit(child, prefix)
        visit(ast.parse(source))
        missing = set(symbols) - definitions.keys()
        if missing:
            raise ValueError(f'Unknown qualified symbols: {sorted(missing)}')
        result['definitions'] = [
            {'symbol': name, 'line': first_line(definitions[name]),
             'source': complete_source(source, definitions[name]),
             'complete': True}
            for name in symbols]
    files.append(result)
print(json.dumps({'files': files}))
