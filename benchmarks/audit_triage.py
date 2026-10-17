"""Audit completed triage workers outside timing, without editing their workspaces.

Requires all four native worker records to be complete before any snapshot/fixture
copy or test execution. The measured tasks use three tests; this evaluator also
runs all 32 frozen tests in independent copies with the required example fixtures.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT/'benchmarks/agent-results/2026-10-16-triage'
NAMES = {f'{condition}_{replicate}' for condition in ('ordinary_triage','jumpai_triage') for replicate in (1,2)}


def digest(path):
    """Return the SHA-256 digest of file bytes; propagate missing-file and read errors."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes(root, expected):
    """Return hashes for the requested relative paths, using None for missing files."""
    return {relative: digest(root / relative) if (root / relative).is_file() else None for relative in expected}


def audit(output):
    """Verify completed workers against frozen source and rerun checks in disposable copies."""
    output = output.resolve()
    inputs=json.loads((output/'inputs.json').read_text())
    expected=json.loads((output/'expected.json').read_text())
    records=json.loads((output/'runs.json').read_text())
    # Native collection can contain an unfinished task; refuse partial auditing.
    workers={record['name']:record for record in records if record['name'] in NAMES}
    if set(workers)!=NAMES or not all(record.get('completed') for record in workers.values()):
        raise SystemExit('Audit deferred: all four measured workers must finish and have completed native records first.')
    baseline=inputs['baseline_sha256']
    baseline_root=Path(inputs['baseline'])
    if hashes(baseline_root,baseline)!=baseline:
        raise SystemExit('Frozen evaluator baseline changed; refusing audit.')
    # Audit originals first. The healthy catalog must exactly match its frozen
    # baseline; all seven other implementation/test files must remain unchanged.
    results = {}
    for name in sorted(NAMES):
        workspace=Path(inputs['workers'][name]['workspace'])
        actual = hashes(workspace, baseline)
        try:
            final=json.loads(workers[name].get('final') or '')
        except (ValueError,TypeError):
            final = None
        results[name]={'workspace':str(workspace),'source_sha256_before_audit':actual,
                       'exact_original_source_restored':actual==baseline,
                       'catalog_exactly_restored':actual['jumpai/catalog.py']==baseline['jumpai/catalog.py'],
                       'other_seven_files_unchanged':all(actual[p]==h for p,h in baseline.items() if p!='jumpai/catalog.py'),
                       'final_json_matches_expected':final==expected,
                       'native_collector_correct':workers[name].get('correct'),
                       'mismatched_source_files':[p for p in baseline if actual[p]!=baseline[p]]}
    # Freeze required fixtures for evaluator tests only. No fixture is introduced
    # into a measured workspace. They are not part of the timed task's inputs.
    fixture_paths=sorted(p for p in (ROOT/'examples').rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix!='.pyc')
    fixture_hashes = {str(p.relative_to(ROOT)): digest(p) for p in fixture_paths}
    audit_root=Path(tempfile.mkdtemp(prefix='jumpai-triage-audit-',dir='/private/tmp'))
    fixtures=audit_root/'fixtures'
    for source in fixture_paths:
        destination = fixtures / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    logdir=output/'audit-logs'
    logdir.mkdir(exist_ok=True)
    for name,result in results.items():
        workspace=Path(result['workspace'])
        replica = audit_root / name
        # Copy only the original source/test inventory, excluding worker scratch
        # and bytecode. This is the exact repaired implementation under audit.
        for relative in baseline:
            destination = replica / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if (workspace/relative).is_file():
                shutil.copy2(workspace / relative, destination)
        shutil.copytree(fixtures/'examples',replica/'examples')
        command=[sys.executable,'-m','unittest','discover','-s','tests','-p','test_*.py','-v']
        env=dict(os.environ,PYTHONPATH=os.pathsep.join([str(replica),str(replica/'tests')]),PYTHONDONTWRITEBYTECODE='1')
        start = time.perf_counter()
        try:
            completed = subprocess.run(command, cwd=replica, env=env, capture_output=True, text=True, timeout=60)
            stdout, stderr, exit_code = (completed.stdout, completed.stderr, completed.returncode)
        except subprocess.TimeoutExpired as exc:
            stdout=(exc.stdout or b'').decode(errors='replace') if isinstance(exc.stdout,bytes) else (exc.stdout or '')
            stderr=(exc.stderr or b'').decode(errors='replace') if isinstance(exc.stderr,bytes) else (exc.stderr or '')
            stderr+='\nEvaluator audit timed out after 60 seconds.\n'
            exit_code = None
        elapsed = time.perf_counter() - start
        log = logdir / f'{name}.txt'
        log.write_text(stdout + stderr)
        matches=re.findall(r'Ran (\d+) tests? in ',stdout+stderr)
        count = int(matches[-1]) if matches else None
        result['full_frozen_suite']={'copy':str(replica),'command':command,'exit_code':exit_code,'test_count':count,
                                    'expected_test_count':32,'passed':exit_code==0 and count==32,
                                    'wall_seconds':elapsed,'log_path':str(log),'log_sha256':digest(log),
                                    'outside_measured_agent_interval':True}
        after = hashes(workspace, baseline)
        result['measured_sources_unchanged_by_audit']=after==result['source_sha256_before_audit']
        result['audit_copy_sources_unchanged_by_tests']=hashes(replica,baseline)==result['source_sha256_before_audit']
    fixtures_unchanged = all((digest(ROOT / relative) == h and digest(fixtures / relative) == h for relative, h in fixture_hashes.items()))
    document={'audit_timestamp_utc':datetime.now(timezone.utc).isoformat(),
              'all_workers_completed_before_audit':True,'baseline_sha256':baseline,
              'fixture_provenance':{'source_root':str(ROOT),'paths_sha256':fixture_hashes,'frozen_copy_root':str(fixtures),
                                    'reason':'Frozen existing tests install examples/programs and examples/workflows and read examples/measurements.json.',
                                    'added_only_to_independent_audit_copies':True,'source_and_copy_unchanged':fixtures_unchanged},
              'workers':results,
              'all_passed':fixtures_unchanged and all(r['exact_original_source_restored'] and r['final_json_matches_expected'] and r['full_frozen_suite']['passed'] and r['measured_sources_unchanged_by_audit'] and r['audit_copy_sources_unchanged_by_tests'] for r in results.values())}
    (output/'audit.json').write_text(json.dumps(document,indent=2)+'\n')
    print(json.dumps({'all_passed':document['all_passed'],'workers':len(results),'audit':str(output/'audit.json')}))
    return 0 if document['all_passed'] else 1


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=DEFAULT)
    args = parser.parse_args()
    raise SystemExit(audit(args.output))
