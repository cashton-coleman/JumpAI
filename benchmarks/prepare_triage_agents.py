"""Freeze and prepare a limited controlled-regression benchmark, never measured agents.

The seeded bug and expected repair live only in evaluator preparation, not in reusable
programs, workflow instructions, or worker prompts. Reruns reuse the immutable
source fixture under benchmarks/fixtures rather than an old report directory.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT/'benchmarks/agent-results/2026-10-16-triage'
BASE = ROOT / 'benchmarks/fixtures/triage-baseline'
WORK = Path('/private/tmp/jumpai-triage-benchmark')
HOME = ROOT/'.jumpai/triage-benchmark'
TESTS = ['test_jumpai.CatalogTests.test_cache_hit_skips_search_and_survives_reopen',
         'test_jumpai.CatalogTests.test_cache_is_scoped_and_invalidated_on_registration',
         'test_recipes.RecipeTests.test_saved_command_replays_without_discovery_or_contract_reads']
GOOD = 'return " ".join(query.casefold().split())'
BAD = 'return " ".join(query.split())'
COUNTS = {'tests':3,'passed':3,'failures':0,'errors':0}
EXPECTED = {'initial':dict(COUNTS,passed=2,failures=1),'final':COUNTS,
            'changed_files':['jumpai/catalog.py'],'repair_applied':True,'status':'passed'}


def write(path, value):
    """Write a JSON artifact with readable indentation; propagate filesystem errors."""
    path.write_text(json.dumps(value,indent=2)+'\n')


def snapshot(name, broken=True):
    """Copy the frozen baseline into a new workspace and optionally seed the known regression."""
    target = WORK / name
    if target.exists():
        raise SystemExit(f'Refusing to overwrite an existing benchmark workspace: {target}')
    shutil.copytree(BASE, target)
    if broken:
        path=target/'jumpai/catalog.py'
        text = path.read_text()
        assert text.count(GOOD) == 1
        path.write_text(text.replace(GOOD, BAD))
    return target


def inputs(root):
    """Return the selected source path and test IDs for the supplied workspace."""
    return {'root':str(root),'tests':TESTS,'source_path':'jumpai/catalog.py'}


def prepare():
    """Verify the frozen baseline and create isolated worker inputs without dispatching agents."""
    OUT.mkdir(parents=True, exist_ok=True)
    hashes=json.loads((ROOT/'benchmarks/fixtures/triage-baseline-hashes.json').read_text())
    for path,digest in hashes.items():
        assert hashlib.sha256((BASE / path).read_bytes()).hexdigest() == digest, path
    WORK.mkdir(exist_ok=True)
    write(OUT / 'baseline-hashes.json', hashes)
    workers = {}
    for condition in ('ordinary_triage','jumpai_triage'):
        for i in (1,2):
            name = f'{condition}_{i}'
            workspace = WORK / name
            if not workspace.exists():
                snapshot(name)
            workers[name]={'workspace':str(workspace),'input':inputs(workspace)}
    write(OUT/'expected.json',EXPECTED)
    write(OUT/'inputs.json',{'baseline':str(BASE),'baseline_sha256':hashes,'workers':workers,'tests':TESTS,
         'seeded_regression':{'path':'jumpai/catalog.py','original':GOOD,'mutated':BAD}})
    (OUT/'protocol.md').write_text('''# Limited agent comparison: controlled regression triage

Four fresh agents, two per condition, same inherited model and reasoning effort, empty conversation history. Each gets a separate writable snapshot of the actual JumpAI implementation and its existing tests. The evaluator deliberately removed case folding from query_key; this is a seeded regression, not a production incident. Agent prompts do not disclose the known defect or fix.

Both conditions must run the same three existing tests, diagnose the failure, apply a minimal source repair, rerun, and return the same small report. Ordinary agents may batch commands and code freely. JumpAI agents use a cached conditional workflow that gathers evidence, pauses for the agent to choose an exact patch, applies it and reruns checks. Reusable programs contain no diagnosis, known repair or fixture-specific source transformation. Healthy checks skip the decision and repair branch.

Dispatch the saved prompt text directly, with fork_turns="none". Do not add an assignment-file read. Run measured workers sequentially; use the same final-response requirement. Setup, program authoring, catalog verification, recipe seeding and evaluator audit are outside the measured agent interval and must be disclosed as amortized preparation.

Collect native rollout token counters (input, cached input, output, reasoning subset, total), model-response counts and elapsed task start-to-completion time. Cached input is already included in input and reasoning is already included in output. Count model exchanges from token_count events with info/last_token_usage, including the final response; separately report model-visible tool calls. These are pilot observations (n=2 each), with uncontrolled provider prompt-cache state.

Audit final JSON, exact restoration of original source, unchanged other frozen source/test files, and full frozen test suite after timing. Do not share expected repairs or another worker's artifacts with workers.
''')
    return workers


def seed(workers):
    """Register and verify task capabilities, then save workspace-scoped replay commands."""
    sys.path.insert(0, str(ROOT))
    from jumpai.catalog import Catalog
    from jumpai.runner import Runner
    from jumpai.workflow import WorkflowSessions
    fixture=WORK/'component-verification'
    fixture.mkdir(exist_ok=True)
    (fixture/'tests').mkdir(exist_ok=True)
    (fixture/'tests/test_sample.py').write_text('import unittest\nclass Smoke(unittest.TestCase):\n def test_ok(self): self.assertEqual(2+2,4)\n')
    (fixture/'sample.py').write_text('old\n')
    catalog = Catalog(HOME)
    runner = Runner(catalog, fixture)
    healthy={'tests':1,'passed':1,'failures':0,'errors':0,'ok':True,'evidence':[]}
    evidence={'path':'sample.py','sha256':hashlib.sha256(b'old\n').hexdigest(),'evidence_ref':str(fixture/'.triage-evidence.json'),'failures':[],'test_source':[],'source':[]}
    specs=[('run-checks',{'root':str(fixture),'tests':['test_sample.Smoke.test_ok']},healthy),
           ('collect-evidence',{'root':str(fixture),'path':'sample.py','checks':healthy},evidence),
           ('apply-replacement',{'root':str(fixture),'patch':{'path':'sample.py','old':'old','new':'new'},'allowed_path':'sample.py','expected_sha256':evidence['sha256']},{'applied':True,'changed_files':['sample.py']}),
           ('report',{'initial':healthy,'final':healthy,'patch':{'applied':False,'changed_files':[]}},{'initial':dict(COUNTS,tests=1,passed=1),'final':dict(COUNTS,tests=1,passed=1),'changed_files':[],'repair_applied':False,'status':'passed'})]
    verified = []
    for directory,params,expected in specs:
        manifest=ROOT/'benchmarks/triage-support'/directory/'manifest.json'
        target = catalog.register(manifest)['id']
        cases = OUT / f'verify-{directory}.json'
        write(cases,[{'input':params,'output':expected}])
        verified.append(runner.verify(target, cases))
    catalog.register(ROOT/'workflows/regression-triage.json')
    target='repo.regression-triage@1.0.1'
    broken=snapshot('workflow-verification-broken')
    healthy_root=snapshot('workflow-verification-healthy',False)
    decision={'path':'jumpai/catalog.py','old':BAD,'new':GOOD}
    cases=OUT/'verify-workflow.json'
    write(cases,[{'input':inputs(broken),'decisions':{'repair':decision},'output':EXPECTED},
                {'input':inputs(healthy_root),'output':{'initial':COUNTS,'final':COUNTS,'changed_files':[],'repair_applied':False,'status':'passed'}}])
    verified.append(Runner(catalog, broken).verify(target, cases))
    cards = {}
    for name,worker in workers.items():
        if not name.startswith('jumpai'):
            continue
        # Save the known successful invocation template for each independent workspace;
        # no workflow execution/repair occurs in measured workspaces before dispatch.
        catalog.save_recipe('regression-triage',target,worker['input'],Path(worker['workspace']),'',catalog.revision)
        command=shlex.join([sys.executable,str(ROOT/'skills/jumpai/scripts/jumpai.py'),'--home',str(HOME),'jump','regression-triage','--resumable','--cwd',worker['workspace']])
        cards[name]={'command':command,'home':str(HOME),'scope':worker['workspace']}
    write(OUT/'verification.json',verified)
    write(OUT/'recipe-cards.json',cards)
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
        if name.startswith('ordinary'):
            prompt=common+'''Use ordinary shell/Python tools. Do not invoke JumpAI as an execution accelerator or access its program catalog; the snapshot's application tests naturally import JumpAI code. You are free to batch inspection, commands and code in whatever way is efficient. Run unittest with this snapshot and its tests directory on PYTHONPATH (for example PYTHONPATH=.:tests python3 -m unittest ... from the snapshot). Create any helper code you need yourself.
'''
        else:
            card = cards[name]
            resume=shlex.join([sys.executable,str(ROOT/'skills/jumpai/scripts/jumpai.py'),'--home',str(HOME),'resume'])
            prompt=common+f'''Use the prepared JumpAI workflow for testing, collecting failure evidence, applying your selected repair and reporting. This recipe card is sufficient; no skill, search or contract read is needed. Start it with:
{card['command']}
It runs checks, skips repair if healthy, or returns status=needs_decision with session_id and decision containing step, prompt, schema and compact source/failure context. You must diagnose and choose the exact replacement; the workflow contains no known repair. If more evidence is necessary, inspect the referenced local evidence file or your snapshot. Resume the returned session with:
{resume} SESSION_ID --step STEP_NAME --decision JSON --cwd {shlex.quote(workspace)}
Supply a JSON object matching the returned schema, using safe shell quoting or a structured command. On completion, summary is the requested final report. Do not rebuild the supplied program mechanics. Do not call result/status unless needed to resolve an actual problem.
'''
        (OUT / f'{name}.prompt.txt').write_text(prompt)


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed',action='store_true',help='verify bundles/workflow, save recipes and write ready-to-dispatch prompts')
    args = parser.parse_args()
    workers = prepare()
    if args.seed:
        prompts(workers, seed(workers))
    print(json.dumps({'workers':list(workers),'output':str(OUT),'seeded':args.seed}))
