"""Read-only installed library, writable workspace caches and workflow sessions."""

import hashlib
from pathlib import Path
import shlex
import sys

from .catalog import Catalog
from .schema import JumpError, canonical, parse_json, validate
from .runtime import DEFAULT_EXECUTION, check_execution


# Published metadata is validated before creating or modifying workspace state.
LIBRARY_SCHEMA = {
    'type': 'object',
    'required': ['format', 'revision', 'versions'],
    'properties': {
        'format': {'type': 'integer', 'enum': [1]},
        'revision': {'type': 'integer', 'minimum': 0},
        'versions': {
            'type': 'array',
            'items': {
                'type': 'object',
                'required': ['id', 'name', 'version', 'manifest', 'digest',
                             'validated', 'enabled', 'created'],
                'properties': {
                    **{key: {'type': 'string', 'minLength': 1}
                       for key in ('id', 'name', 'version', 'manifest', 'digest')},
                    'validated': {'type': 'integer', 'enum': [0, 1]},
                    'enabled': {'type': 'integer', 'enum': [0, 1]},
                    'created': {'type': 'number'},
                },
            },
        },
    },
}


def read_library(library):
    """Return raw bytes and checked metadata; raise JumpError for invalid exports.

    Reading never opens the shared SQLite database or writes to the library.
    Artifact integrity remains the runner's responsibility at execution time.
    """
    try:
        raw = Path(library).read_bytes()
    except OSError as exc:
        raise JumpError(f'Installed library unavailable at {library}; reinstall to publish it') from exc

    data = parse_json(raw)
    validate(data, LIBRARY_SCHEMA, 'library')
    check_execution(data.get('execution', DEFAULT_EXECUTION))
    seen = set()

    # Reject inconsistent identities before any records reach the local catalog.
    for row in data['versions']:
        manifest = parse_json(row['manifest'])
        if not isinstance(manifest, dict):
            raise JumpError('Library manifest must be an object')
        if (row['name'] != manifest.get('name') or row['version'] != manifest.get('version')
                or row['id'] != f"{row['name']}@{row['version']}"):
            raise JumpError('Library identity does not match its manifest')
        if row['id'] in seen:
            raise JumpError(f"Duplicate library version: {row['id']}")
        seen.add(row['id'])
        if manifest.get('kind') not in ('program', 'workflow'):
            raise JumpError('Library manifest kind must be program or workflow')
        if 'artifact' not in row:
            raise JumpError('Library version is missing its artifact field')
        artifact = row['artifact']
        if manifest['kind'] == 'program':
            if not isinstance(artifact, str) or not Path(artifact).is_absolute():
                raise JumpError('Library program artifact must be an absolute path')
        elif artifact is not None:
            raise JumpError('Library workflow must not have a program artifact')

    return raw, data


def publish(home):
    """Export definitions and host settings, never cached inputs, runs or decisions."""
    from .install import atomic_write
    catalog = Catalog(home)
    try:
        with catalog.db:
            catalog.db.execute('BEGIN')
            payload = {'format': 1, 'origin': str(catalog.home), 'revision': catalog.revision,
                       'execution': catalog.execution_settings(),
                       'versions': [dict(row) for row in catalog.db.execute('SELECT * FROM versions ORDER BY id')]}
        path = catalog.home / 'library.json'
        text = canonical(payload) + '\n'
        if not path.exists() or path.read_text() != text:
            atomic_write(path, text)
        return str(path)
    finally:
        catalog.close()


def open_workspace(library, workspace):
    """Synchronize an installed library and return an open workspace Catalog.

    Preserve local caches and sessions, reject conflicting or older metadata,
    and roll back failed imports. The caller must close the returned catalog.
    """
    library = Path(library).expanduser().resolve()
    root = Path(workspace).expanduser().resolve()
    raw, data = read_library(library)

    # Resolve symlinks before placing mutable state beneath the task workspace.
    identity = hashlib.sha256(str(library).encode()).hexdigest()[:16]
    home = (root / '.jumpai/runtime' / identity).resolve()
    if not home.is_relative_to(root):
        raise JumpError('Workspace runtime path escapes the task workspace')
    catalog = Catalog(home)
    digest = hashlib.sha256(raw).hexdigest()
    try:
        with catalog.db:
            catalog.db.execute(
                'CREATE TABLE IF NOT EXISTS library_sync '
                '(source TEXT PRIMARY KEY, digest TEXT NOT NULL, revision INTEGER NOT NULL)'
            )

        # Synchronize definitions atomically, preserving local caches and sessions.
        with catalog.db:
            catalog.db.execute('BEGIN IMMEDIATE')
            old = catalog.db.execute(
                'SELECT digest,revision FROM library_sync WHERE source=?', (str(library),)
            ).fetchone()
            if old and old['digest'] == digest:
                return catalog
            if old and old['revision'] > data['revision']:
                raise JumpError('Installed library revision moved backwards; refusing stale metadata')

            execution = data.get('execution', DEFAULT_EXECUTION)
            changed = catalog.execution_settings() != execution
            catalog.db.execute("INSERT OR REPLACE INTO settings VALUES ('execution', ?)",
                               (canonical(execution),))
            manifests = []
            for row in data['versions']:
                manifest = parse_json(row['manifest'])
                existing = catalog.db.execute('SELECT * FROM versions WHERE id=?', (row['id'],)).fetchone()
                if existing and existing['digest'] != row['digest']:
                    raise JumpError(f"Local version conflicts with installed library: {row['id']}")
                catalog.db.execute('INSERT OR IGNORE INTO programs(name,kind) VALUES (?,?)', (row['name'], manifest['kind']))
                if not existing:
                    columns = ['id', 'name', 'version', 'manifest', 'digest',
                               'artifact', 'validated', 'enabled', 'created']
                    catalog.db.execute(
                        'INSERT INTO versions (' + ','.join(columns) + ') VALUES ('
                        + ','.join('?' for _ in columns) + ')',
                        tuple(row[k] for k in columns),
                    )
                    manifests.append((row['id'], manifest))
                    changed = True
                elif any(existing[k] != row[k] for k in ('validated', 'enabled', 'artifact')):
                    catalog.db.execute(
                        'UPDATE versions SET validated=?,enabled=?,artifact=? WHERE id=?',
                        (row['validated'], row['enabled'], row['artifact'], row['id']),
                    )
                    changed = True

            # All versions exist before inserting foreign-key references.
            for target, manifest in manifests:
                catalog.db.execute(
                    'INSERT INTO discovery(id,name,purpose,use_cases,tags) VALUES (?,?,?,?,?)',
                    (target, manifest['name'], manifest['purpose'],
                     ' '.join(manifest.get('use_cases', [])), ' '.join(manifest.get('tags', []))),
                )
                for position, step in enumerate(manifest.get('steps', [])):
                    if 'target' in step:
                        catalog.db.execute(
                            'INSERT INTO workflow_steps(workflow_id,position,step_name,target_id,bindings) '
                            'VALUES (?,?,?,?,?)',
                            (target, position, step['name'], step['target'], canonical(step['bindings'])),
                        )

            # Invalidate bindings only when capability definitions or availability change.
            if changed:
                catalog.bump()
            catalog.db.execute(
                'INSERT INTO library_sync(source,digest,revision) VALUES (?,?,?) '
                'ON CONFLICT(source) DO UPDATE SET digest=excluded.digest,revision=excluded.revision',
                (str(library), digest, data['revision']),
            )
        return catalog
    except (KeyError, TypeError, AttributeError) as exc:
        catalog.close()
        raise JumpError(f'Invalid installed library metadata: {exc}') from exc
    except BaseException:
        catalog.close()
        raise


def prepared_handoff(catalog, prefix, root, source, tests):
    """Run diagnosis once and return a self-contained child decision assignment.

    Stop before source edits. The parent receives a pinned resume command; a
    healthy run returns its completed receipt without requiring delegation.
    """
    from .runner import Runner
    from .workflow import WorkflowSessions

    receipt = WorkflowSessions(Runner(catalog, root)).start(
        'repo.regression-triage@1.4.0',
        {'root': root, 'source_path': source, 'tests': tests},
    )
    if receipt['status'] != 'needs_decision':
        return receipt

    # Give the child evidence and judgment work, not redundant execution commands.
    return {
        'status': receipt['status'],
        'session_id': receipt['session_id'],
        'child_assignment': {
            'instruction': (
                'Diagnose using only this checkpoint. Do not call tools, read files, '
                'rerun checks, or delegate. Return JSON with decision {path,old,new} '
                'and a short reason. If a specific missing fact prevents a safe '
                'decision, return needs_evidence listing that fact instead of guessing. '
                'Do not repeat the checkpoint in your reply.'
            ),
            'checkpoint': receipt['decision'],
        },
        'resume_command': shlex.join(prefix + [
            'resume', receipt['session_id'], '--step', receipt['decision']['step'],
        ]),
        'parent_instruction': (
            'Pass child_assignment unchanged to one fresh diagnosis child. '
            'Wait for its result with a blocking wait, not short polling. Review its '
            'decision and append --decision JSON to resume_command. The workflow '
            'guards the source digest, applies the repair, runs final checks, and '
            'returns the report. Do not reread supplied evidence or repeat checks. '
            'If needs_evidence is returned, obtain only the identified missing context.'
        ),
    }


def handoff(catalog, library, workspace, tests, source, read_only, *, prepare=False):
    """Return quoted commands for a child using the parent's workspace state.

    Reject invalid test IDs and escaping source paths. The read-only flag is
    assignment guidance; source-edit authority remains with the caller.
    """
    if not isinstance(tests, list) or not all(isinstance(test, str) and test.strip() for test in tests):
        raise JumpError('Handoff tests must be an array of nonempty unittest IDs')
    if source is not None and (not isinstance(source, str) or not source.strip()):
        raise JumpError('Handoff source must be a nonempty workspace-relative path')
    if prepare and (not source or not tests or not read_only):
        raise JumpError('Prepared handoff requires a source, tests, and a read-only diagnosis child')

    # Bind every command to the same catalog and workspace for parent resumption.
    root = str(Path(workspace).resolve())
    launcher = Path(__file__).resolve().parents[1] / 'skills/jumpai/scripts/jumpai.py'
    prefix = [sys.executable, str(launcher)]
    prefix += (
        ['--library', str(Path(library).resolve()), '--workspace', root]
        if library else ['--home', str(catalog.home), '--workspace', root]
    )

    # Validate source scope before running preparation or constructing commands.
    if source:
        source_path = Path(source)
        if source_path.is_absolute() or not (Path(root) / source_path).resolve().is_relative_to(Path(root)):
            raise JumpError('Handoff source must be a workspace-relative path')

    # A matching workflow owns its checks and evidence; do not offer duplicate commands.
    commands = {}
    if source and tests:
        catalog.available('repo.regression-triage@1.4.0')
        if prepare:
            return prepared_handoff(catalog, prefix, root, source, tests)
        commands['diagnose'] = shlex.join(prefix + [
            'run', '--target', 'repo.regression-triage@1.4.0', '--resumable',
            '--input', canonical({'root': root, 'tests': tests, 'source_path': source}),
        ])
    elif tests:
        catalog.available('triage.run-checks@1.0.1')
        commands['checks'] = shlex.join(prefix + [
            'run', '--target', 'triage.run-checks@1.0.1',
            '--input', canonical({'root': root, 'tests': tests}),
        ])
    elif source:
        catalog.available('dev.source-snapshot@1.1.1')
        commands['source_snapshot'] = shlex.join(prefix + [
            'run', '--target', 'dev.source-snapshot@1.1.1',
            '--input', canonical({'root': root, 'files': [{'path': source, 'include_text': True}]}),
        ])

    # Keep the permission boundary explicit in the child assignment card.
    return {
        'workspace': root,
        'catalog': str(catalog.home),
        'command_prefix': shlex.join(prefix),
        'source_edits_allowed': not read_only,
        'instruction': (
            'Use the matching commands directly, without another skill/search/contract read. '
            'For diagnosis, start diagnose: it runs checks and returns complete failure evidence at a decision checkpoint. '
            + ('Stop there: return the receipt/session ID and your diagnosis to the parent; do not resume or edit source/tests.'
               if read_only else
               'Choose the minimal repair and resume the returned session; the workflow applies it, rechecks, and reports.')
        ),
        'commands': commands,
    }
