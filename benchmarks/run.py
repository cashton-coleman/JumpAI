"""Paired local execution benchmark with explicit, reproducible payload token counts.

This replays commands, not model inference. It cannot measure thinking tokens or
model-generation latency. See methodology.md before interpreting results.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import shlex
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from jumpai.catalog import Catalog
from jumpai.schema import canonical


def write(path, value):
    """Write a JSON artifact with readable indentation; propagate filesystem errors."""
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')


def run_command(command):
    """Run a shell command with a deadline and return elapsed time and captured streams."""
    started = time.perf_counter_ns()
    result = subprocess.run(command, shell=True, executable='/bin/bash', cwd=ROOT,
                            text=True, capture_output=True, timeout=60)
    elapsed = (time.perf_counter_ns() - started) / 1000000
    if result.returncode:
        raise RuntimeError(f'Command failed ({result.returncode}): {command}\n{result.stderr}\n{result.stdout}')
    return (elapsed, result.stdout, result.stderr)


def fixtures(directory):
    """Create independent validation inputs and expected outputs in a new fixture directory."""
    directory.mkdir()
    (directory / 'sample.py').write_text('''import unittest
class Example(unittest.TestCase):
    def test_one(self): pass
    async def test_two(self): pass
    def helper(self): pass
def test_top(): pass
''')
    (directory / 'exists.md').write_text('present')
    (directory / 'links.md').write_text('''[existing](exists.md#section)
[missing](absent.md)
[external](https://example.com)
[anchor](#section)
```md
[ignored](also-absent.md)
```
''')
    write(directory / 'records.json', [
        {'test':'a','status':'passed','duration_ms':1.0},
        {'test':'b','status':'failed','duration_ms':3.0},
        {'test':'c','status':'error','duration_ms':2.0}])
    cases = {
        'python-test-inventory': [{'input':{'root':str(directory),'files':['sample.py']},
            'output':{'files_scanned':1,'tests':3,'files':[{'file':'sample.py','classes':[{'class':'Example','tests':2}],'test_functions':1,'tests':3}]}}],
        'markdown-links': [{'input':{'root':str(directory),'files':['links.md']},
            'output':{'files_scanned':1,'local_links_checked':2,'broken':[{'file':'links.md','line':2,'target':'absent.md'}]}}],
        'test-run-summary': [{'input':{'path':str(directory / 'records.json'),'top':2},
            'output':{'tests':3,'statuses':{'error':1,'failed':1,'passed':1},'total_test_ms':6.0,'failures':['b','c'],
                      'slowest':[{'test':'b','duration_ms':3.0},{'test':'c','duration_ms':2.0}]}}],
    }
    for name, rows in cases.items():
        write(directory / f'{name}-cases.json', rows)
    return cases


def describe(samples):
    """Return median, range, and nearest-rank p95 for a nonempty sample sequence."""
    values = sorted(samples)
    return {'median':statistics.median(values), 'min':values[0],
            'p95':values[math.ceil(.95 * len(values)) - 1], 'max':values[-1]}


def main():
    """Prepare real inputs and compare command paths; reject failed or mismatched results."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True, help='New results directory; existing paths are never overwritten')
    parser.add_argument('--rounds', type=int, default=30)
    parser.add_argument('--seed', type=int, default=4701)
    args = parser.parse_args()
    if args.rounds < 5:
        parser.error('use at least 5 rounds')
    import tiktoken  # Benchmark-only dependency; core remains stdlib-only.
    encoding = tiktoken.get_encoding('o200k_base')
    count = lambda text: len(encoding.encode(text, disallowed_special=()))
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    captured = output / 'test-outcomes.json'
    python = shlex.quote(sys.executable)
    capture_command = f'{python} benchmarks/capture_tests.py --output {shlex.quote(str(captured))}'
    elapsed, stdout, stderr = run_command(capture_command)
    (output / 'test-capture.log').write_text(stderr)
    case_dir = output / 'fixtures'
    fixture_cases = fixtures(case_dir)
    home = ROOT / '.jumpai' / 'benchmarks' / output.name
    if (home / 'catalog.sqlite3').exists():
        raise RuntimeError(f'Use a unique output name: benchmark catalog already exists at {home}')
    prefix = f'{python} -m jumpai --home {shlex.quote(str(home.relative_to(ROOT)))}'
    tasks = [
        {'folder':'python-test-inventory','label':'Python test inventory','id':'python.test-inventory@1.0.1',
         'query':'inventory python tests','input':{'root':'.','files':['tests/test_jumpai.py']}},
        {'folder':'markdown-links','label':'Local Markdown link audit','id':'docs.local-links@1.0.1',
         'query':'audit local markdown links','input':{'root':'.','files':['README.md','skills/jumpai/SKILL.md','skills/jumpai/references/authoring.md']}},
        {'folder':'test-run-summary','label':'Test run summary','id':'tests.summarize-run@1.0.1',
         'query':'summarize test outcomes','input':{'path':str(captured.relative_to(ROOT)),'top':3}},
    ]
    transcript = []
    def measured(command, category, task):
        """Time a command, check its output, and record the serialized command and response costs."""
        elapsed, stdout, stderr = run_command(command)
        row = {'category':category,'task':task,'command':command,'stdout':stdout,'stderr':stderr,
               'wall_ms':elapsed,'command_tokens':count(canonical({'cmd':command,'workdir':str(ROOT)})),
               'stdout_tokens':count(stdout),'stderr_tokens':count(stderr)}
        row['payload_tokens'] = row['command_tokens'] + row['stdout_tokens'] + row['stderr_tokens']
        transcript.append(row)
        return row
    # Register all versions before remembering any; each mutation invalidates bindings.
    for task in tasks:
        manifest = f"programs/{task['folder']}/manifest.json"
        task['registration'] = measured(f'{prefix} register {manifest}', 'register', task['folder'])
        cases = str((case_dir / f"{task['folder']}-cases.json").relative_to(ROOT))
        task['verification'] = measured(f'{prefix} verify {task["id"]} --cases {shlex.quote(cases)}', 'verify', task['folder'])
    input_paths = {'tests/test_jumpai.py', 'README.md', 'skills/jumpai/SKILL.md', 'skills/jumpai/references/authoring.md', str(captured.relative_to(ROOT))}
    provenance = {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in sorted(input_paths)}
    raw_samples = []
    for task in tasks:
        source_path = f"programs/{task['folder']}/main.py"
        source = (ROOT / source_path).read_text()
        data = canonical(task['input'])
        suffix = f" <<'JUMPAI_INPUT'\n{data}\nJUMPAI_INPUT"
        commands = {
            'fresh_code': f'{python} -B -c {shlex.quote(source)}' + suffix,
            'saved_script': f'{python} -B {shlex.quote(source_path)}' + suffix,
            'jumpai_cached': f'{prefix} run --query {shlex.quote(task["query"])} --input {shlex.quote(data)}',
        }
        task['commands'] = commands
        # First encounter includes discovery, contract retrieval, execution and binding.
        task['discovery'] = measured(f'{prefix} resolve {shlex.quote(task["query"])}', 'resolve_miss', task['folder'])
        if json.loads(task['discovery']['stdout'])['cache_hit']:
            raise AssertionError('Expected first discovery to miss')
        task['inspection'] = measured(f'{prefix} inspect {task["id"]}', 'inspect', task['folder'])
        task['first_run'] = measured(f'{prefix} run --target {task["id"]} --input {shlex.quote(data)} --remember {shlex.quote(task["query"])}', 'first_run_remember', task['folder'])
        if 'remembered' not in json.loads(task['first_run']['stdout']):
            raise AssertionError('First execution did not create a resolution')
        task['expected'] = json.loads(task['first_run']['stdout'])['output']
        # Keep source artifact files independent of the SQLite catalog and registration.
        task['source_tokens'] = count(source)
        task['manifest_tokens'] = count((ROOT / f"programs/{task['folder']}/manifest.json").read_text())
        task['fixture_tokens'] = count(json.dumps(fixture_cases[task['folder']], indent=2))
        # One untimed warm-up per variant; timings below are steady-state OS-cache timings.
        for variant, command in commands.items():
            _, text, _ = run_command(command)
            value = json.loads(text)
            if variant == 'jumpai_cached':
                assert value['cache_hit'] is True
                value = value['output']
            if canonical(value) != canonical(task['expected']):
                raise AssertionError(f'Output mismatch: {task["folder"]}/{variant}')
    rng = random.Random(args.seed)
    for round_number in range(args.rounds):
        task_order = tasks[:]
        rng.shuffle(task_order)
        for task in task_order:
            variants = list(task['commands'])
            rng.shuffle(variants)
            for variant in variants:
                row = measured(task['commands'][variant], variant, task['folder'])
                value = json.loads(row['stdout'])
                if variant == 'jumpai_cached':
                    assert value['cache_hit'] is True
                    value = value['output']
                if canonical(value) != canonical(task['expected']):
                    raise AssertionError('Benchmark result changed')
                raw_samples.append({'round':round_number, 'task':task['folder'], 'variant':variant,
                                    **{key:row[key] for key in ('wall_ms','command_tokens','stdout_tokens','stderr_tokens','payload_tokens')}})
    result_tasks = []
    for task in tasks:
        variants = {}
        for variant in task['commands']:
            rows = [r for r in raw_samples if r['task'] == task['folder'] and r['variant'] == variant]
            variants[variant] = {key:describe([r[key] for r in rows]) for key in ('wall_ms','command_tokens','stdout_tokens','payload_tokens')}
        fresh = variants['fresh_code']
        cached = variants['jumpai_cached']
        saved = variants['saved_script']
        token_saving = fresh['payload_tokens']['median'] - cached['payload_tokens']['median']
        # Artifact-text amortization is a proxy, not observed model authoring cost.
        initial_asset_tokens = task['source_tokens'] + task['manifest_tokens'] + task['fixture_tokens']
        setup_rows = [task[key] for key in ('registration','verification','discovery','inspection','first_run')]
        setup_tokens = initial_asset_tokens + sum(row['payload_tokens'] for row in setup_rows)
        delta = cached['wall_ms']['median'] - fresh['wall_ms']['median']
        result_tasks.append({
            'task':task['folder'],'label':task['label'],'input':task['input'],'output':task['expected'],
            'variants':variants,'payload_tokens_saved_vs_fresh':token_saving,
            'payload_savings_percent_vs_fresh':100 * token_saving / fresh['payload_tokens']['median'],
            'payload_tokens_extra_vs_saved_script':cached['payload_tokens']['median'] - saved['payload_tokens']['median'],
            'execution_overhead_ms_vs_fresh':delta,
            'first_discovery_and_run_wall_ms':sum(task[key]['wall_ms'] for key in ('discovery','inspection','first_run')),
            'first_discovery_and_run_payload_tokens':sum(task[key]['payload_tokens'] for key in ('discovery','inspection','first_run')),
            'registration_and_verification_wall_ms':task['registration']['wall_ms']+task['verification']['wall_ms'],
            'asset_text_tokens':initial_asset_tokens,'setup_payload_plus_asset_tokens':setup_tokens,
            'artifact_text_break_even_uses_vs_fresh':max(1, math.ceil((setup_tokens - cached['payload_tokens']['median']) / token_saving)) if token_saving > 0 else None,
            'commands':task['commands'],
        })
    catalog = Catalog(home)
    stats = catalog.stats()
    catalog.close()
    report = {'timestamp_utc':datetime.now(timezone.utc).isoformat(),
        'python':sys.version,'platform':platform.platform(),'machine':platform.machine(),
        'rounds':args.rounds,'random_seed':args.seed,'tokenizer':'tiktoken/o200k_base',
        'tiktoken_version':tiktoken.__version__,'actual_model_usage':None,
        'model_generation_time_measured':False,'tool_transport_time_measured':False,
        'test_capture_wall_ms':elapsed,'test_capture_command':capture_command,
        'input_sha256':provenance,'catalog_stats':stats,'tasks':result_tasks,
        'limitations':['No LLM inference was run by this harness; timings are subprocess wall time.',
            'Token counts cover serialized command arguments plus stdout/stderr only, under o200k_base.',
            'No claim of actual model/billed/hidden-reasoning tokens, prompt caching or end-to-end turn latency.',
            'Fresh code is supplied in full each invocation; already-saved scripts are a separate control.',
            'All paths use identical source and return the same task output; JumpAI adds metadata.',
            'Cases are small real local workloads, not a representative cross-repository agent benchmark.',
            'Warm OS caches; paired randomized order; no cold boot or isolated machine.',
            'Skill reading and code/manifest authoring time are excluded; asset-text amortization is a proxy.']}
    write(output / 'results.json', report)
    write(output / 'samples.json', raw_samples)
    write(output / 'transcript.json', transcript)
    lines = ['# JumpAI local reuse benchmark', '',
        '**These are execution timings and tokenizer-counted payloads, not total agent time or billed model tokens.**', '',
        f'{args.rounds} measured runs per task/variant; randomized paired order; identical source and task outputs.', '',
        '| Task | Fresh code ms | Saved script ms | JumpAI cached ms | Fresh payload tokens | Saved payload tokens | JumpAI payload tokens | Reduction vs fresh |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for task in result_tasks:
        v = task['variants']
        times = [v[k]['wall_ms']['median'] for k in ('fresh_code','saved_script','jumpai_cached')]
        tokens = [v[k]['payload_tokens']['median'] for k in ('fresh_code','saved_script','jumpai_cached')]
        lines.append(f"| {task['label']} | {times[0]:.2f} | {times[1]:.2f} | {times[2]:.2f} | {tokens[0]:g} | {tokens[1]:g} | {tokens[2]:g} | {task['payload_savings_percent_vs_fresh']:.1f}% |")
    lines += ['', '## First use and setup', '',
        '| Task | Register + verify ms | Discovery + inspect + first run ms | First discovery/run payload tokens | Setup plus artifact-text tokens | Proxy break-even uses vs rewriting |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for task in result_tasks:
        lines.append(f"| {task['label']} | {task['registration_and_verification_wall_ms']:.2f} | {task['first_discovery_and_run_wall_ms']:.2f} | {task['first_discovery_and_run_payload_tokens']} | {task['setup_payload_plus_asset_tokens']} | {task['artifact_text_break_even_uses_vs_fresh']} |")
    lines += ['', 'The setup estimate counts source, manifest, fixture text and setup command/output payloads once. It does not measure the model tokens spent creating or debugging those artifacts. Existing saved scripts do not need the catalog setup.', '',
              '## Scope and reproducibility', '',
              f'Tokenizer: `{report["tokenizer"]}`, package `{report["tiktoken_version"]}`. Python: `{platform.python_version()}`; machine: `{platform.machine()}`.', '',
              'Inputs: the actual JumpAI test source, its three Markdown documents, and recorded outcomes from running all 27 core tests. Verification uses separate small fixtures containing missing links and failed tests; these fixtures are not the measured workloads.', '',
              'All measured outputs matched. JumpAI cache hits were asserted. Input hashes, per-run timings, complete commands and outputs are saved beside this report.', '',
              *['- '+item for item in report['limitations']], '',
              'No tokens/second conversion is used: local execution timing does not establish model generation latency.', '']
    (output / 'report.md').write_text('\n'.join(lines))
    print('\n'.join(lines[:12]))
    print(f'Full report: {output / "report.md"}')


if __name__ == '__main__':
    main()
