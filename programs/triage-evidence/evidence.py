"""Bounded, verbatim failure evidence. Never infer a diagnosis or a replacement."""

import ast
import copy
import hashlib
import json
from pathlib import Path
import re


def first_line(node):
    """Return the first decorator or definition line for a syntax-tree node."""
    return min([node.lineno] + [d.lineno for d in getattr(node, 'decorator_list', [])])


def complete_source(source, node):
    """Return a verbatim definition including decorators without mutating its syntax node."""
    located = copy.copy(node)
    located.lineno = first_line(node)
    return ast.get_source_segment(source, located)


def encoded(value):
    """Encode evidence deterministically for byte-budget accounting."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True)


def scoped(root, relative):
    """Resolve a workspace-relative path and reject paths or symlinks that escape it."""
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError('evidence path escapes workspace')
    return path


def definitions(source):
    """Return qualified function names and syntax nodes without importing the source."""
    found = []
    def visit(node, prefix=''):
        """Walk nested definitions while retaining qualified names and their syntax nodes."""
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + child.name
                if not isinstance(child, ast.ClassDef):
                    found.append((name, child))
                visit(child, name + '.')
            else:
                visit(child, prefix)
    visit(ast.parse(source))
    return found


def calls(node):
    """Return directly named functions and methods referenced by calls in a syntax node."""
    return {n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id
            for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))}


def collect(data):
    """Read scoped failure evidence, save the full artifact, and return a bounded packet."""
    # Resolve the source and exact test identities before selecting snippets.
    root = Path(data['root']).resolve()
    path = scoped(root, data['path'])
    raw = path.read_bytes()
    source = raw.decode('utf-8')
    source_defs = definitions(source)
    selected = set()
    stack_symbols = set()
    tests = []
    failures = []
    unavailable = []
    for failure in data['checks']['evidence']:
        test_id = failure['test']
        parts = test_id.rsplit('.', 2)
        if len(parts) != 3:
            unavailable.append(test_id)
        else:
            module, class_name, test_name = parts
            # unittest modules may be top-level test_x or package-qualified.
            module_path = Path(*module.split('.')).with_suffix('.py')
            candidates = [scoped(root, module_path), scoped(root, Path('tests') / module_path)]
            test_path = next((p for p in candidates if p.is_file()), None)
            if test_path is None:
                unavailable.append(test_id)
            else:
                test_source = test_path.read_text()
                matches = [(name, node) for name, node in definitions(test_source) if name == class_name + '.' + test_name]
                if not matches:
                    unavailable.append(test_id)
                for _, node in matches:
                    tests.append({'test': test_id, 'source': complete_source(test_source, node)})
                    selected.update((name for name, candidate in source_defs if candidate.name in calls(node)))
        traceback = failure['traceback']
        frames = []
        for filename, line, function in re.findall(r'File "([^"]+)", line (\d+), in ([^\n]+)', traceback):
            frame_path = Path(filename)
            frame_path = (frame_path if frame_path.is_absolute() else root / frame_path).resolve()
            if frame_path.is_relative_to(root):
                frames.append({'path': str(frame_path.relative_to(root)), 'line': int(line), 'function': function})
                if frame_path == path:
                    stack_symbols.update((name for name, node in source_defs if node.name == function.strip()))
                    selected.update(stack_symbols)
        failures.append({'test': test_id, 'error': traceback.rstrip().split('\n')[-1], 'frames': frames})
    # Add one layer of direct module-level helpers used by the selected methods.
    # Keep qualified identities; same-named methods are never silently overwritten.
    initial_selection = set(selected)
    for name, node in source_defs:
        if name in initial_selection:
            helpers = {n.func.id for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
            selected.update(q for q, helper in source_defs if '.' not in q and helper.name in helpers)
    snippets = [{'symbol': name, 'line': first_line(node), 'source': complete_source(source, node)}
                for name, node in source_defs if name in selected]
    full = {'path': data['path'], 'sha256': hashlib.sha256(raw).hexdigest(),
            'failures': failures, 'test_source': tests, 'source': snippets,
            'raw_failures': data['checks']['evidence'], 'unavailable_tests': unavailable}
    # Retain complete evidence separately from the bounded checkpoint packet.
    artifact = scoped(root, '.triage-evidence.json')
    artifact.write_text(json.dumps(full, indent=2) + '\n')
    packet = {'path': full['path'], 'sha256': full['sha256'], 'evidence_ref': str(artifact),
              'excerpt_policy': 'Verbatim complete definitions, including decorators; enclosing class/module context is not included.',
              'failures': [], 'test_source': [], 'source': [],
              'omitted': {'failures': [], 'tests': list(unavailable), 'symbols': []}}
    # Drop entire low-priority blocks when needed, never silently truncate a body.
    # Prioritize the failing stack and related transformations over incidental
    # methods (cleanup, statistics, etc.) called by the test.
    critical_helpers = {n.func.id for name, node in source_defs if name in stack_symbols
                        for n in ast.walk(node) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    related = {name for name, node in source_defs if calls(node) & critical_helpers}
    def priority(item):
        """Rank snippets by relevance to the failure, then length and qualified name."""
        name = item['symbol']
        rank = 0 if name in critical_helpers else 1 if name in stack_symbols else 2 if name in related else 3
        return rank, len(item['source']), name
    packet['failures'] = failures.copy()
    packet['test_source'] = tests.copy()
    packet['source'] = sorted(snippets, key=priority)
    budget = data.get('max_bytes', 3900)
    if type(budget) is not int or not 1024 <= budget <= 3900:
        raise ValueError('max_bytes must be an integer between 1024 and 3900')
    for key, omitted_key, identifier in [('source', 'symbols', 'symbol'), ('test_source', 'tests', 'test'), ('failures', 'failures', 'test')]:
        while len(encoded(packet).encode()) > budget and packet[key]:
            removed = packet[key].pop()
            packet['omitted'][omitted_key].append(removed[identifier])
    if len(encoded(packet).encode()) > budget:
        # Omitted identifier lists themselves can be arbitrarily large.
        packet['omitted'] = {key: {'count': len(values), 'details': str(artifact)} for key, values in packet['omitted'].items()}
    if len(encoded(packet).encode()) > budget:
        raise ValueError('Evidence metadata exceeds budget; shorten file paths or increase max_bytes')
    return packet
