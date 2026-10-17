"""Prepare fresh matched checkpoint-evidence workers without measured agent dispatch."""

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT=ROOT/'benchmarks/agent-results/2026-10-16-evidence'
BASE=ROOT/'benchmarks/fixtures/triage-baseline'
WORK=Path('/private/tmp/jumpai-evidence-benchmark')
HOME=ROOT/'.jumpai/evidence-benchmark'
CONDITIONS=['plain_evidence','old_evidence','full_evidence']
TESTS=['test_jumpai.CatalogTests.test_cache_hit_skips_search_and_survives_reopen','test_jumpai.CatalogTests.test_cache_is_scoped_and_invalidated_on_registration','test_recipes.RecipeTests.test_saved_command_replays_without_discovery_or_contract_reads']
GOOD='return " ".join(query.casefold().split())'
BAD='return " ".join(query.split())'
COUNTS={'tests':3,'passed':3,'failures':0,'errors':0}
EXPECTED={'initial':dict(COUNTS,passed=2,failures=1),'final':COUNTS,'changed_files':['jumpai/catalog.py'],'repair_applied':True,'status':'passed'}

def write(path,value):
    """Write a JSON artifact with readable indentation; propagate filesystem errors."""
    path.write_text(json.dumps(value,indent=2)+'\n')

def snapshot(name,broken=True):
    """Copy the frozen baseline into a new workspace and optionally seed the known regression."""
    target = WORK / name
    if target.exists():
        raise RuntimeError(f'Refusing to overwrite existing workspace: {target}')
    shutil.copytree(BASE, target)
    if broken:
        path=target/'jumpai/catalog.py'
        source = path.read_text()
        assert source.count(GOOD) == 1
        path.write_text(source.replace(GOOD, BAD))
    return target

def parameters(root):
    """Return the selected source path and test IDs for the supplied workspace."""
    return {'root':str(root),'tests':TESTS,'source_path':'jumpai/catalog.py'}

def prepare():
    """Verify the frozen baseline and create isolated worker inputs without dispatching agents."""
    OUT.mkdir(parents=True, exist_ok=True)
    WORK.mkdir(exist_ok=True)
    hashes=json.loads((ROOT/'benchmarks/fixtures/triage-baseline-hashes.json').read_text())
    assert all((hashlib.sha256((BASE / p).read_bytes()).hexdigest() == h for p, h in hashes.items()))
    if (OUT/'inputs.json').exists():
        existing=json.loads((OUT/'inputs.json').read_text())
        assert existing['baseline_sha256']==hashes
        return existing['workers']
    workers = {}
    for condition in CONDITIONS:
        for replicate in (1,2):
            name = f'{condition}_{replicate}'
            workspace = snapshot(name)
            workers[name]={'workspace':str(workspace),'input':parameters(workspace)}
    write(OUT/'inputs.json',{'baseline':str(BASE),'baseline_sha256':hashes,'workers':workers,'tests':TESTS,'seeded_regression':{'path':'jumpai/catalog.py','original':GOOD,'mutated':BAD}})
    write(OUT/'expected.json',EXPECTED)
    write(OUT/'baseline-hashes.json',hashes)
    return workers

def seed(workers):
    """Register and verify task capabilities, then save workspace-scoped replay commands."""
    sys.path.insert(0, str(ROOT))
    from jumpai.catalog import Catalog
    from jumpai.runner import Runner
    catalog = Catalog(HOME)
    # Register immutable versions before validating; prepare fresh disposable fixtures.
    fixture=WORK/'component-verification'
    fixture.mkdir(exist_ok=True)
    (fixture/'tests').mkdir(exist_ok=True)
    (fixture/'tests/test_sample.py').write_text('import unittest\nclass Smoke(unittest.TestCase):\n def test_ok(self): self.assertEqual(2+2,4)\n')
    (fixture/'sample.py').write_text('old\n')
    healthy={'tests':1,'passed':1,'failures':0,'errors':0,'ok':True,'evidence':[]}
    evidence={'path':'sample.py','sha256':hashlib.sha256(b'old\n').hexdigest(),'evidence_ref':str(fixture/'.triage-evidence.json'),'failures':[],'test_source':[],'source':[]}
    specs=[('run-checks',{'root':str(fixture),'tests':['test_sample.Smoke.test_ok']},healthy),('collect-evidence',{'root':str(fixture),'path':'sample.py','checks':healthy},evidence),('apply-replacement',{'root':str(fixture),'patch':{'path':'sample.py','old':'old','new':'new'},'allowed_path':'sample.py','expected_sha256':evidence['sha256']},{'applied':True,'changed_files':['sample.py']}),('report',{'initial':healthy,'final':healthy,'patch':{'applied':False,'changed_files':[]}},{'initial':dict(COUNTS,tests=1,passed=1),'final':dict(COUNTS,tests=1,passed=1),'changed_files':[],'repair_applied':False,'status':'passed'})]
    verified = []
    runner = Runner(catalog, fixture)
    for directory,params,expected in specs:
        manifest=ROOT/'benchmarks/triage-support'/directory/'manifest.json'
        target = catalog.register(manifest)['id']
        cases = OUT / f'verify-{directory}.json'
        write(cases, [{'input': params, 'output': expected}])
        verified.append(runner.verify(target, cases))
    new_manifest=ROOT/'programs/triage-evidence/manifest.json'
    new_version=json.loads(new_manifest.read_text())['version']
    workflow=ROOT/'workflows/regression-triage-complete.json'
    workflow_version=json.loads(workflow.read_text())['version']
    catalog.register(new_manifest)
    # New collector is verified against its own representative maintained fixtures.
    new_cases=ROOT/'programs/triage-evidence/cases.json'
    if not new_cases.exists():
        raise RuntimeError('New evidence fixture file must be supplied before seeding')
    verified.append(Runner(catalog, ROOT).verify(f'triage.collect-evidence@{new_version}', new_cases))
    for version,manifest in [('1.0.1',ROOT/'workflows/regression-triage.json'),(workflow_version,workflow)]:
        catalog.register(manifest)
        broken = snapshot(f'verify-{new_version}-{version}-broken')
        healthy_root = snapshot(f'verify-{new_version}-{version}-healthy', False)
        cases = OUT / f'verify-workflow-{version}.json'
        write(cases,[{'input':parameters(broken),'decisions':{'repair':{'path':'jumpai/catalog.py','old':BAD,'new':GOOD}},'output':EXPECTED},{'input':parameters(healthy_root),'output':{'initial':COUNTS,'final':COUNTS,'changed_files':[],'repair_applied':False,'status':'passed'}}])
        verified.append(Runner(catalog, broken).verify(f'repo.regression-triage@{version}', cases))
    cards = {}
    for name,worker in workers.items():
        if name.startswith('plain_'):
            continue
        version='1.0.1' if name.startswith('old_') else workflow_version
        recipe = f'regression-triage-{version}'
        catalog.save_recipe(recipe,f'repo.regression-triage@{version}',worker['input'],Path(worker['workspace']),'',catalog.revision)
        command=shlex.join([sys.executable,str(ROOT/'skills/jumpai/scripts/jumpai.py'),'--home',str(HOME),'jump',recipe,'--resumable','--cwd',worker['workspace']])
        cards[name]={'command':command,'home':str(HOME),'scope':worker['workspace'],'target':f'repo.regression-triage@{version}'}
    write(OUT / f'verification-{new_version}.json', verified)
    write(OUT / 'verification.json', verified)
    write(OUT / 'recipe-cards.json', cards)
    catalog.close()
    return cards

def prompts(workers,cards):
    """Write worker assignments from prepared inputs and command cards; do not dispatch them."""
    for name,worker in workers.items():
        workspace=worker['workspace']
        common=f'''You are an independent benchmark worker. Complete this task end to end in your writable repository snapshot: {workspace}
This is a deliberately seeded regression in a copy of an actual repository, not a production incident. Diagnose and minimally repair the failure in jumpai/catalog.py. Do not modify tests or other source files. Run these three existing tests before and after the repair: {json.dumps(TESTS)}.
Use only your snapshot, scratch files beneath it, and the execution mechanism described below. Do not inspect the original repository, evaluator scripts, expected results, other agents' workspaces or benchmark artifacts. No network, delegation or user questions. Do not read skills or catalog implementation outside your snapshot. You may inspect any needed source/test files within your snapshot.
Finish with JSON only, with fields initial and final (each exactly tests, passed, failures, errors as integer counts), changed_files (relative source paths changed), repair_applied (boolean), and status (passed if all selected final tests pass, else failed). Report actual measurements; no invented results. The exact source change will be audited independently.
'''
        if name.startswith('plain_'):
            prompt=common+'''Use ordinary shell/Python tools. Do not invoke JumpAI as an execution accelerator or access its program catalog; the snapshot's application tests naturally import JumpAI code. You are free to batch inspection, commands and code in whatever way is efficient. Run unittest with this snapshot and its tests directory on PYTHONPATH (for example PYTHONPATH=.:tests python3 -m unittest ... from the snapshot). Create any helper code you need yourself.
'''
        else:
            resume=shlex.join([sys.executable,str(ROOT/'skills/jumpai/scripts/jumpai.py'),'--home',str(HOME),'resume'])
            prompt=common+f'''Use the prepared JumpAI workflow for testing, collecting failure evidence, applying your selected repair and reporting. This recipe card is sufficient; no skill, search or contract read is needed. Start it with:
{cards[name]['command']}
It runs checks, skips repair if healthy, or returns status=needs_decision with session_id and decision containing step, prompt, schema and source/failure context. You must diagnose and choose the exact replacement; the workflow contains no known repair. Read the evidence's completeness and omission metadata when present: complete definitions are verbatim, and omissions explicitly identify any truncation or missing context. The workflow checks the recorded source digest before applying your decision. Decide whether the supplied evidence is sufficient; if more evidence is necessary, inspect the referenced local evidence file or any needed source/test file within your snapshot. Resume the returned session with:
{resume} SESSION_ID --step STEP_NAME --decision JSON --cwd {shlex.quote(workspace)}
Supply a JSON object matching the returned schema, using safe shell quoting or a structured command. On completion, summary is the requested final report. Do not rebuild the supplied program mechanics. Do not call result/status unless needed to resolve an actual problem.
'''
        (OUT / f'{name}.prompt.txt').write_text(prompt)

def seed_updated_version():
    """Publish a new candidate without recreating or touching worker snapshots."""
    sys.path.insert(0, str(ROOT))
    from jumpai.catalog import Catalog
    from jumpai.runner import Runner
    workers=json.loads((OUT/'inputs.json').read_text())['workers']
    before={name:{relative:hashlib.sha256((Path(worker['workspace'])/relative).read_bytes()).hexdigest() for relative in json.loads((OUT/'baseline-hashes.json').read_text())} for name,worker in workers.items()}
    manifest=ROOT/'programs/triage-evidence/manifest.json'
    evidence_version=json.loads(manifest.read_text())['version']
    workflow=ROOT/'workflows/regression-triage-complete.json'
    version=json.loads(workflow.read_text())['version']
    catalog = Catalog(HOME)
    catalog.register(manifest)
    verified=[Runner(catalog,ROOT).verify(f'triage.collect-evidence@{evidence_version}',ROOT/'programs/triage-evidence/cases.json')]
    catalog.register(workflow)
    broken = snapshot(f'updated-{version}-broken')
    healthy = snapshot(f'updated-{version}-healthy', False)
    cases = OUT / f'verify-workflow-{version}.json'
    write(cases,[{'input':parameters(broken),'decisions':{'repair':{'path':'jumpai/catalog.py','old':BAD,'new':GOOD}},'output':EXPECTED},{'input':parameters(healthy),'output':{'initial':COUNTS,'final':COUNTS,'changed_files':[],'repair_applied':False,'status':'passed'}}])
    verified.append(Runner(catalog, broken).verify(f'repo.regression-triage@{version}', cases))
    cards=json.loads((OUT/'recipe-cards.json').read_text())
    for name,worker in workers.items():
        if name.startswith('plain_'):
            continue
        selected='1.0.1' if name.startswith('old_') else version
        recipe = f'regression-triage-{selected}'
        catalog.save_recipe(recipe,f'repo.regression-triage@{selected}',worker['input'],Path(worker['workspace']),'',catalog.revision)
        command=shlex.join([sys.executable,str(ROOT/'skills/jumpai/scripts/jumpai.py'),'--home',str(HOME),'jump',recipe,'--resumable','--cwd',worker['workspace']])
        cards[name]={'command':command,'home':str(HOME),'scope':worker['workspace'],'target':f'repo.regression-triage@{selected}'}
    write(OUT/'recipe-cards.json',cards)
    write(OUT / f'verification-{version}.json', verified)
    # Keep the prior shared-component verification and current new-target record.
    previous=json.loads((OUT/'verification.json').read_text())
    write(OUT/'verification.json',[item for item in previous if item.get('id') not in {f'triage.collect-evidence@{evidence_version}',f'repo.regression-triage@{version}'}]+verified)
    prompts({name:worker for name,worker in workers.items() if name.startswith('full_')},cards)
    after={name:{relative:hashlib.sha256((Path(worker['workspace'])/relative).read_bytes()).hexdigest() for relative in before[name]} for name,worker in workers.items()}
    assert after==before,'Measured worker source changed during targeted reseeding'
    write(OUT/f'targeted-reseed-{version}.json',{'version':version,'all_worker_sources_unchanged':True,'before':before,'after':after,'rewritten_prompts':[name for name in workers if name.startswith('full_')]})
    catalog.close()
    return {'version':version,'workers_untouched':True,'verification':verified}


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', action='store_true')
    args = parser.parse_args()
    workers = prepare()
    if args.seed:
        prompts(workers, seed(workers))
    print(json.dumps({'workers':list(workers),'output':str(OUT),'seeded':args.seed}))
