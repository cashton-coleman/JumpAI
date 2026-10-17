"""Install skill discovery and host startup guidance as one managed lifecycle."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import sqlite3
import sys
import tempfile
import tomllib

from .catalog import Catalog
from .runner import Runner
from .schema import JumpError

# Managed markers and pinned bundles shared by installation and health checks.
REPOSITORY = Path(__file__).resolve().parents[1]
STANDARDS = REPOSITORY / 'skills/jumpai/AGENTS.override.md'
BEGIN = '\n<!-- jumpai:begin -->\n'
END = '<!-- jumpai:end -->\n'
BUNDLES = [
    'programs/source-snapshot/manifest.json',
    'benchmarks/triage-support/run-checks/manifest.json',
    'benchmarks/triage-support/apply-replacement/manifest.json',
    'benchmarks/triage-support/report/manifest.json',
    'programs/triage-evidence/manifest.json',
    'workflows/regression-triage-complete.json',
    'programs/report-combine/manifest.json',
]


def read(path):
    """Read a UTF-8 file, or return empty text when absent; propagate I/O errors."""
    return path.read_text(encoding='utf-8') if path.exists() else ''


def replace_block(text, block):
    """Replace only managed guidance; reject malformed or duplicate marker pairs."""
    if text.count('<!-- jumpai:begin -->') != text.count(BEGIN) or text.count(BEGIN) != text.count(END) or text.count(BEGIN) > 1:
        raise JumpError('Malformed or duplicate JumpAI instruction markers; refusing to change this file')
    if BEGIN not in text:
        return text + block
    start = text.index(BEGIN)
    end = text.index(END) + len(END)
    if end < start:
        raise JumpError('Reversed JumpAI instruction markers')
    return text[:start] + block + text[end:]


def atomic_write(path, text):
    """Replace a UTF-8 file atomically while preserving its existing permissions.

    Follow an existing symlink to its target and remove staging files on failure.
    Propagate write errors; this does not promise durability across power loss.
    """
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.jumpai-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(text)
        if path.exists():
            os.chmod(name, path.stat().st_mode & 0o777)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def guidance(skill, catalog):
    """Embed bundled standards and quoted commands; reject an incomplete skill package."""
    standards = STANDARDS.read_text(encoding='utf-8').strip()
    if not standards:
        raise JumpError('Bundled working standards are empty; restore the complete skill package')
    command = shlex.join([sys.executable, str(skill / 'scripts/jumpai.py'), '--library', str(catalog / 'library.json')])
    return BEGIN + standards + '\n\n' + f'''Use JumpAI for reusable coding mechanics before writing equivalent ad hoc scripts: source inspection, guarded edits, preparation, checks, and chained workflows. Use known recipes/pinned contracts directly; otherwise load the jumpai skill at {skill / 'SKILL.md'} and resolve once. Skip trivial commands or work with no suitable capability; capture substantial reusable mechanics after completing the task. Do not repeatedly search or reread known contracts.
JumpAI command prefix: {command}
Requested usage footer command: {command} usage --footer. This reads local Codex telemetry without opening the catalog; do not run programs merely to make a turn qualify for the averages.
Installed library: {catalog / 'library.json'} is read-only. Run from the actual task workspace or add --workspace /absolute/task/path before the subcommand; mutable state stays in that workspace's .jumpai/runtime. No write to the shared catalog is needed. Use matching installed capabilities, rather than only reading the skill and recreating their mechanics. Respect user instructions, task scope, and host permissions.
Installation detects a usable C++17 toolchain and stores the runtime preference in SQLite; settings shows it when needed for native program authoring. The runner selects C++ or a declared Python fallback for C++ bundles; existing Python bundles remain supported.
Native binaries are cached in the workspace runtime. Compiler identification is cached; macOS/BSD dependency watches avoid repeated header scans, with conservative checks on unsupported hosts. Changed source/toolchains rebuild in the background; use Python fallback while validation/building is pending, or continue other work for native-only programs. Do not repeatedly poll. Run receipts expose native_build status; explicit verify waits for compilation.
For Python unittest diagnosis with known source and tests: run --target repo.regression-triage@1.4.0 --resumable --input '{{"root":"/absolute/task/path","source_path":"relative.py","tests":["test_module.Class.test_name"]}}'. It checks tests and pauses with evidence before any source edit. Choose a repair and resume only when source edits are authorized. For checks alone: run --target triage.run-checks@1.0.1 --input '{{"root":"/absolute/task/path","tests":["test_module"]}}'.
For delegated unittest diagnosis, you must run --workspace CHILD_WORKSPACE handoff --prepare --read-only --source RELATIVE_FILE --tests 'JSON_ARRAY'. It runs initial checks once and returns child_assignment plus resume_command. Pass child_assignment unchanged to one fresh child: it must use only the enclosed evidence and return a decision without tools, reads, checks, or further delegation. The parent waits with a blocking wait (60000 ms where supported), reviews the decision, and resumes once; final checks and reporting are built in. A healthy prepared run needs no child. Do not reread skills/contracts, repeat supplied evidence or checks, or poll in short intervals. If a specific missing fact prevents a safe decision, the child returns needs_evidence; retrieve only that missing context. For other mechanics use the applicable pinned command. Respect explicit user exceptions, including ordinary-tool comparison controls.
''' + END


def seed(catalog_path):
    """Verify bundled mechanics in disposable fixtures; leave user work untouched."""
    catalog = Catalog(catalog_path)
    try:
        targets = [catalog.register(REPOSITORY / path)['id'] for path in BUNDLES]
        pending = {target for target in targets if not catalog.get(target)['validated']}
        if not pending:
            return targets
        with tempfile.TemporaryDirectory(prefix='jumpai-install-') as temp:
            root = Path(temp).resolve()
            source = 'def normalize(value):\n    return value.strip()\n'
            (root / 'sample.py').write_text(source)
            (root / 'test_sample.py').write_text('import unittest\nfrom sample import normalize\nclass Check(unittest.TestCase):\n    def test_normalize(self):\n        self.assertEqual(normalize(" a "), "a")\n')
            digest = hashlib.sha256(source.encode()).hexdigest()
            good = {'tests': 1, 'passed': 1, 'failures': 0, 'errors': 0, 'ok': True, 'evidence': []}
            bad = {'tests': 1, 'passed': 0, 'failures': 1, 'errors': 0}
            def report(initial, applied):
                """Build the expected fixture report without executing a repair."""
                counts = ('tests', 'passed', 'failures', 'errors')
                return {
                    'initial': {key: initial[key] for key in counts},
                    'final': {key: good[key] for key in counts},
                    'changed_files': ['sample.py'] if applied else [],
                    'repair_applied': applied,
                    'status': 'passed',
                }

            runner = Runner(catalog, root)

            def verify(target, cases):
                """Verify pending targets against temporary fixtures; propagate failures."""
                if target not in pending:
                    return
                fixture = root / 'cases.json'
                fixture.write_text(json.dumps(cases))
                runner.verify(target, fixture)

            # Verify programs before the workflow that composes them.
            verify(targets[0], [{'input': {'root': str(root), 'files': [{'path': 'sample.py', 'symbols': ['normalize']}]}, 'output': {'files': [{'path': 'sample.py', 'sha256': digest, 'bytes': len(source.encode()), 'definitions': [{'symbol': 'normalize', 'line': 1, 'source': source.rstrip(), 'complete': True}]}]}}])
            verify(targets[1], [{'input': {'root': str(root), 'tests': ['test_sample.Check']}, 'output': good}])
            verify(targets[2], [{'input': {'root': str(root), 'allowed_path': 'sample.py', 'expected_sha256': digest, 'patch': {'path': 'sample.py', 'old': 'value.strip()', 'new': 'value'}}, 'output': {'applied': True, 'changed_files': ['sample.py']}}])
            (root / 'sample.py').write_text(source)
            verify(targets[3], [{'input': {'initial': good, 'final': good, 'patch': {'applied': False, 'changed_files': []}}, 'output': report(good, False)}])
            verify(targets[4], [{'input': {'root': str(root), 'path': 'sample.py', 'checks': {'evidence': []}}, 'output': {'path': 'sample.py', 'sha256': digest, 'evidence_ref': str(root / '.triage-evidence.json'), 'excerpt_policy': 'Verbatim complete definitions, including decorators; enclosing class/module context is not included.', 'failures': [], 'test_source': [], 'source': [], 'omitted': {'failures': [], 'tests': [], 'symbols': []}}}])
            broken = root / 'broken'
            broken.mkdir()
            (broken / 'sample.py').write_text(source.replace('value.strip()', 'value'))
            (broken / 'test_sample.py').write_text((root / 'test_sample.py').read_text())
            verify(targets[5], [
                {'input': {'root': str(root), 'source_path': 'sample.py', 'tests': ['test_sample.Check']}, 'output': report(good, False)},
                {'input': {'root': str(broken), 'source_path': 'sample.py', 'tests': ['test_sample.Check']}, 'decisions': {'repair': {'path': 'sample.py', 'old': 'return value', 'new': 'return value.strip()'}}, 'output': report(bad, True)},
            ])
            verify(targets[6], json.loads((REPOSITORY / 'programs/report-combine/cases.json').read_text()))
        return targets
    finally:
        catalog.close()


def paths(args):
    """Resolve discovery, instruction, receipt, and profile paths from CLI options.

    Honor an active Codex override; require explicit paths for generic hosts.
    """
    profile = Path(args.codex_home).expanduser().resolve()
    if args.host == 'generic' and (not args.skills_dir or not args.instructions):
        raise JumpError('Generic hosts require --skills-dir and --instructions for their startup discovery mechanism')
    skill = Path(args.skills_dir).expanduser().absolute() / 'jumpai' if args.skills_dir else Path.home() / '.agents/skills/jumpai'
    instructions = Path(args.instructions).expanduser().absolute() if args.instructions else profile / 'AGENTS.md'
    if not args.instructions and read(profile / 'AGENTS.override.md').strip():
        instructions = profile / 'AGENTS.override.md'
    return skill, instructions, skill.parent / '.jumpai-install.json', profile


def doctor(state, profile):
    """Return installation health without modifying host files or shared SQLite."""
    from .workspace import read_library

    profile = Path(state.get('codex_home', profile))
    skill = Path(state['skill'])
    instructions = Path(state['instructions'])
    issues = []
    if not skill.is_symlink() or skill.resolve() != Path(state['source']):
        issues.append('Skill link missing or changed')
    if not (skill / 'scripts/jumpai.py').is_file():
        issues.append('Skill runtime checkout unavailable')
    if not (skill / 'scripts/native_build.py').is_file():
        issues.append('Native background-build worker unavailable; reinstall from a complete checkout')
    if not (skill / 'scripts/cache_guard.py').is_file():
        issues.append('Native dependency watcher unavailable; reinstall from a complete checkout')
    if state['block'] not in read(instructions):
        issues.append('Startup instruction block missing or changed')
    standards = read(skill / 'AGENTS.override.md').strip()
    if not standards or standards not in state['block']:
        issues.append('Bundled working standards missing or outdated in startup guidance; reinstall')
    if state['host'] == 'codex' and instructions.parent == profile:
        override = profile / 'AGENTS.override.md'
        effective = override if read(override).strip() else profile / 'AGENTS.md'
        if effective != instructions:
            issues.append(f'Startup guidance shadowed by {effective}; reinstall to reconcile')
        config = tomllib.loads(read(profile / 'config.toml'))
        limit = config.get('project_doc_max_bytes', 32768)
        text = read(instructions)
        if END in text and len(text[:text.index(END) + len(END)].encode()) > limit:
            issues.append('Startup block exceeds the configured instruction byte limit')
        for item in config.get('skills', {}).get('config', []):
            if item.get('enabled') is False and Path(item.get('path', '')).expanduser().resolve() in {skill.resolve(), (skill / 'SKILL.md').resolve()}:
                issues.append('JumpAI skill is explicitly disabled in Codex configuration')
    database = Path(state['catalog']) / 'library.json'
    execution = None
    if not database.is_file():
        issues.append('Installed read-only library missing; reinstall to publish it')
    else:
        try:
            _, published = read_library(database)
        except JumpError as exc:
            issues.append(str(exc))
        else:
            from .runtime import DEFAULT_EXECUTION
            execution = published.get('execution', dict(DEFAULT_EXECUTION))
            versions = {row['id']: row for row in published['versions']}
            for target in state['targets']:
                row = versions.get(target, {})
                if (row.get('validated'), row.get('enabled')) != (1, 1):
                    issues.append(f'Capability unavailable or unvalidated: {target}')
    return {'healthy': not issues, 'issues': issues, 'skill': str(skill), 'instructions': str(instructions), 'catalog': state['catalog'], 'targets': state['targets'], 'agent_handoff': state['block'],
            'execution': execution, 'toolchain_probe_failures': state.get('toolchain_probe_failures', [])}


def manage(args):
    """Plan, install, inspect, or remove managed files without replacing user work.

    Return a plan or status. Reject ownership conflicts and preserve the catalog
    on uninstall; roll back startup-file changes when installation writes fail.
    """
    skill, instructions, receipt, profile = paths(args)
    previous = json.loads(receipt.read_text()) if receipt.exists() else None
    if args.action in ('doctor', 'uninstall'):
        if previous is None:
            return {'healthy': False, 'issues': ['No managed installation receipt'], 'receipt': str(receipt)}
        if args.action == 'doctor':
            return doctor(previous, profile)
        skill = Path(previous['skill'])
        instructions = Path(previous['instructions'])
        original = read(instructions)
        if BEGIN in original and previous['block'] not in original:
            raise JumpError('Managed block edited; refusing to remove changed instructions')
        if skill.is_symlink() and skill.resolve() != Path(previous['source']):
            raise JumpError('Skill link changed; refusing to remove it')
        if skill.exists() and not skill.is_symlink():
            raise JumpError('Skill directory replaced; refusing to remove it')
        atomic_write(instructions, original.replace(previous['block'], '', 1))
        if skill.is_symlink():
            skill.unlink()
        receipt.unlink()
        return {'uninstalled': True, 'catalog_preserved': previous['catalog']}
    # Validate ownership and prepare the plan before any installation writes.
    source = REPOSITORY / 'skills/jumpai'
    if skill.exists() or skill.is_symlink():
        if not skill.is_symlink() or skill.resolve() != source:
            raise JumpError(f'Refusing to overwrite an unrelated skill at {skill}')
    catalog_path = Path(args.catalog or (previous or {}).get('catalog') or os.environ.get('JUMPAI_HOME', str(Path.home() / '.local/share/jumpai'))).expanduser().resolve()
    block = guidance(skill, catalog_path)
    original = read(instructions)
    updated = replace_block(original, block)
    old_instructions = Path(previous['instructions']) if previous else instructions
    old_text = read(old_instructions)
    if previous and old_instructions != instructions and previous['block'] not in old_text:
        raise JumpError('Previous startup block changed; reconcile it before moving installation')
    plan = {'host': args.host, 'codex_home': str(profile), 'skill': str(skill), 'source': str(source), 'instructions': str(instructions), 'catalog': str(catalog_path), 'block': block, 'receipt': str(receipt)}
    if args.action == 'plan':
        return plan
    targets = seed(catalog_path)
    # Detect native compilation before publishing host settings to workspace users.
    from .runtime import detect_execution
    execution, failures = detect_execution()
    catalog = Catalog(catalog_path)
    try:
        catalog.set_execution_settings(execution)
    finally:
        catalog.close()
    plan['execution'] = execution
    plan['toolchain_probe_failures'] = failures
    from .workspace import publish
    publish(catalog_path)
    # Restore startup/discovery files if installation fails. Verified catalog additions
    # are retained; they cannot erase existing capabilities or caller workspaces.
    linked = skill.is_symlink()
    receipt_text = read(receipt)
    try:
        skill.parent.mkdir(parents=True, exist_ok=True)
        if not linked:
            skill.symlink_to(source, target_is_directory=True)
        atomic_write(instructions, updated)
        if old_instructions != instructions:
            atomic_write(old_instructions, old_text.replace(previous['block'], '', 1))
        plan['targets'] = targets
        atomic_write(receipt, json.dumps(plan, indent=2) + '\n')
    except OSError:
        if not linked and skill.is_symlink():
            skill.unlink()
        if read(instructions) == updated:
            atomic_write(instructions, original)
        if old_instructions != instructions:
            atomic_write(old_instructions, old_text)
        if receipt_text:
            atomic_write(receipt, receipt_text)
        raise
    return doctor(plan, profile)


def main(argv=None):
    """Run an installer action and emit JSON; return 2 for errors or unhealthy state."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['plan', 'install', 'doctor', 'uninstall'])
    parser.add_argument('--host', choices=['codex', 'generic'], default='codex')
    parser.add_argument('--skills-dir', help='Host skill discovery directory; defaults to ~/.agents/skills')
    parser.add_argument('--instructions', help='Host-loaded startup instruction file')
    parser.add_argument('--codex-home', default=os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    parser.add_argument('--catalog', help='Shared catalog; defaults to existing receipt, JUMPAI_HOME, or ~/.local/share/jumpai')
    parser.add_argument('--quiet', action='store_true', help='Omit expanded startup guidance and installation metadata')
    try:
        args = parser.parse_args(argv)
        result = manage(args)
        if args.quiet:
            # Shell users need status and destinations; full guidance remains in the receipt.
            keys = ('healthy', 'issues', 'skill', 'instructions', 'catalog', 'execution', 'uninstalled', 'catalog_preserved')
            result = {key: result[key] for key in keys if key in result}
        print(json.dumps(result, indent=2))
        return 0 if result.get('healthy', True) else 2
    except (OSError, ValueError, JumpError, sqlite3.Error) as exc:
        print(json.dumps({'error': str(exc)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
