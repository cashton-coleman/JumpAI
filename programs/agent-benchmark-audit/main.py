"""Native whole-agent metrics and independent repaired-source verification.

This reusable evaluator reads exported benchmark contracts, never model reasoning.
It writes traces, counters, medians and audits; intermediate data stays off-context.
"""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import tempfile


def write(path,value):
    """Write a JSON artifact with readable indentation; propagate filesystem errors."""
    path.write_text(json.dumps(value,indent=2)+'\n')


def digest(path):
    """Return the SHA-256 digest of file bytes; propagate missing-file and read errors."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hashes(root,inventory):
    """Return hashes for the requested relative paths, using None for missing files."""
    return {p: digest(root / p) if (root / p).is_file() else None for p in inventory}


def parse(path,parent,names,expected):
    """Extract matching worker metrics and tool traces; exclude private reasoning and prompts."""
    rows = []
    with path.open() as stream:
        for line in stream:
            try:
                rows.append(json.loads(line))
            except ValueError:
                # Live logs can end with an incomplete record; completion is checked below.
                continue
    meta=next((r['payload'] for r in rows if r.get('type')=='session_meta'),{})
    source=meta.get('source',{})
    if not isinstance(source, dict):
        return None
    source=source.get('subagent',{}).get('thread_spawn',{})
    name=source.get('agent_path','').rsplit('/',1)[-1]
    if source.get('parent_thread_id') != parent or name not in names:
        return None
    contexts=[r['payload'] for r in rows if r.get('type')=='turn_context']
    events=[r for r in rows if r.get('type')=='event_msg']
    starts=[r for r in events if r['payload'].get('type')=='task_started']
    ends=[r for r in events if r['payload'].get('type')=='task_complete']
    usage=[r for r in events if r['payload'].get('type')=='token_count' and r['payload'].get('info')]
    calls=[r for r in rows if r.get('type')=='response_item' and r['payload'].get('type') in ('custom_tool_call','function_call')]
    final = None
    for row in rows:
        p=row.get('payload',{})
        if row.get('type')=='response_item' and p.get('type')=='message' and p.get('role')=='assistant' and p.get('channel')=='final':
            final=''.join(c.get('text','') for c in p.get('content',[]) if c.get('type') in ('output_text','text'))
    if final is None and ends:
        final = ends[-1]['payload'].get('last_agent_message')
    result={'name':name,'condition':name.rsplit('_',1)[0],'replicate':int(name.rsplit('_',1)[1]),'agent_path':source['agent_path'],'thread_id':meta.get('id'),'rollout_path':str(path),'model':contexts[-1].get('model') if contexts else None,'reasoning_effort':contexts[-1].get('effort') if contexts else None,'completed':bool(ends),'model_tool_calls':len(calls),'model_response_count':len(usage),'final':final}
    if starts:
        result['started_at'] = starts[0]['timestamp']
    if usage:
        u=usage[-1]['payload']['info']['total_token_usage'].copy()
        u['uncached_input_tokens']=u['input_tokens']-u.get('cached_input_tokens',0)
        result['usage']=u
        keys=['input_tokens','cached_input_tokens','output_tokens','reasoning_output_tokens','total_tokens']
        result['counter_sums_match']=all(sum((r['payload']['info'].get('last_token_usage') or {}).get(k,0) for r in usage)==u.get(k,0) for k in keys)
    if ends:
        if not starts:
            raise ValueError(f'{name}: completion without task start')
        result['completed_at']=ends[-1]['timestamp']
        result['wall_seconds']=(datetime.fromisoformat(ends[-1]['timestamp'].replace('Z','+00:00'))-datetime.fromisoformat(starts[0]['timestamp'].replace('Z','+00:00'))).total_seconds()
        result['reported_duration_ms']=ends[-1]['payload'].get('duration_ms')
        try:
            result['correct'] = json.loads(final) == expected
        except (TypeError, ValueError):
            # Non-JSON final output fails the contract rather than stopping the audit.
            result['correct'] = False
    trace = []
    for row in rows:
        p=row.get('payload',{})
        if row.get('type')=='response_item' and p.get('type') in ('custom_tool_call','function_call','custom_tool_call_output','function_call_output'):
            trace.append({'timestamp':row['timestamp'],**{k:p[k] for k in ('type','name','call_id','input','arguments','output') if k in p}})
        elif row.get('type')=='event_msg' and p.get('type')=='token_count' and p.get('info'):
            trace.append({'timestamp':row['timestamp'],'type':'token_count','usage':p['info']['total_token_usage'],'last_usage':p['info'].get('last_token_usage')})
    # Report candidates transparently: mixed read/write calls require human interpretation.
    candidates = []
    for row in calls:
        p = row['payload']
        text = str(p.get('input', p.get('arguments', '')))
        if re.search(r'\b(?:sed|cat|head|tail|rg|read_text|read_bytes|open)\b',text) and re.search(r'(?:catalog\.py|test_jumpai\.py|test_recipes\.py|triage-evidence\.json)',text):
            candidates.append({'timestamp':row['timestamp'],'call_id':p.get('call_id'),'command':text})
    result['source_read_candidates']=candidates
    return (result, trace)


def main(args):
    """Audit completed workers, copied source, selected tests, and native usage; save results."""
    output = Path(args['output_dir']).resolve()
    output.mkdir(parents=True, exist_ok=True)
    config = json.loads((output / 'inputs.json').read_text())
    expected = json.loads((output / 'expected.json').read_text())
    names = set(config['workers'])
    conditions = args['conditions']
    if {n.rsplit('_', 1)[0] for n in names} != set(conditions):
        raise ValueError('Condition/worker mismatch')
    runs = []
    for path in sorted(Path(args['sessions_dir']).glob('*.jsonl')):
        parsed=parse(path,args['parent_thread_id'],names,expected)
        if parsed:
            result, trace = parsed
            runs.append(result)
            if result['completed']:
                write(output / f"{result['name']}.execution.json", trace)
    if len({r['name'] for r in runs}) != len(runs):
        raise ValueError('Duplicate native worker names; refusing ambiguous measurement')
    runs.sort(key=lambda r: r.get('started_at', ''))
    write(output / 'runs.json', runs)
    ready=len(runs)==len(names) and all(r['completed'] for r in runs)
    if not ready:
        return {'ready': False, 'completed': sum((r['completed'] for r in runs)), 'expected': len(names)}
    groups = {}
    for condition in conditions:
        group=[r for r in runs if r['condition']==condition]
        groups[condition]={'completed':len(group),'correct':sum(r['correct'] for r in group),'wall_seconds':statistics.median(r['wall_seconds'] for r in group),'model_tool_calls':statistics.median(r['model_tool_calls'] for r in group),'model_exchanges':statistics.median(r['model_response_count'] for r in group),'usage':{k:statistics.median(r['usage'][k] for r in group) for k in group[0]['usage']}}
    write(output/'summary.json',groups)
    usage_audit={r['name']:{'response_count':r['model_response_count'],'counter_sums_match':r['counter_sums_match']} for r in runs}
    write(output/'usage-audit.json',usage_audit)
    baseline = config['baseline_sha256']
    base = Path(config['baseline'])
    if hashes(base, baseline) != baseline:
        raise ValueError('Frozen evaluator baseline changed')
    audit_root=Path(tempfile.mkdtemp(prefix='jumpai-agent-audit-',dir='/private/tmp'))
    logs = output / 'audit-logs'
    logs.mkdir(exist_ok=True)
    audited = {}
    for run in runs:
        name = run['name']
        workspace = Path(config['workers'][name]['workspace'])
        before = hashes(workspace, baseline)
        replica = audit_root / name
        for relative in baseline:
            destination = replica / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if (workspace / relative).is_file():
                shutil.copy2(workspace / relative, destination)
        env=dict(os.environ,PYTHONPATH=os.pathsep.join([str(replica),str(replica/'tests')]),PYTHONDONTWRITEBYTECODE='1')
        command=[sys.executable,'-m','unittest',*config['tests']]
        completed = subprocess.run(command, cwd=replica, env=env, text=True, capture_output=True, timeout=60)
        log = logs / f'{name}.txt'
        log.write_text(completed.stdout + completed.stderr)
        count=re.findall(r'Ran (\d+) tests? in ',completed.stdout+completed.stderr)
        result={'workspace':str(workspace),'exact_original_source_restored':before==baseline,'source_sha256_before_audit':before,'mismatched_files':[p for p in baseline if before[p]!=baseline[p]],'final_json_matches_expected':run['correct'],'selected_tests':{'command':command,'copy':str(replica),'exit_code':completed.returncode,'test_count':int(count[-1]) if count else None,'expected_test_count':len(config['tests']),'passed':completed.returncode==0 and bool(count) and int(count[-1])==len(config['tests']),'log_path':str(log),'log_sha256':digest(log),'outside_measured_agent_interval':True},'measured_sources_unchanged_by_audit':hashes(workspace,baseline)==before,'audit_copy_sources_unchanged_by_tests':hashes(replica,baseline)==before}
        audited[name] = result
    passed=all(r['exact_original_source_restored'] and r['final_json_matches_expected'] and r['selected_tests']['passed'] and r['measured_sources_unchanged_by_audit'] and r['audit_copy_sources_unchanged_by_tests'] for r in audited.values()) and all(r['counter_sums_match'] for r in runs)
    audit={'audit_timestamp_utc':datetime.now(timezone.utc).isoformat(),'all_workers_completed_before_audit':True,'baseline_sha256':baseline,'workers':audited,'all_passed':passed,'suite_scope':'Same three selected task tests in isolated evaluator copies; exact baseline restoration verifies all frozen implementation/test files. No full-suite repetition.'}
    write(output/'audit.json',audit)
    return {'ready':True,'completed':len(runs),'expected':len(names),'all_passed':passed,'summary':groups,'artifacts':{'runs':str(output/'runs.json'),'audit':str(output/'audit.json'),'summary':str(output/'summary.json')}}


if __name__=='__main__':
    print(json.dumps(main(json.load(sys.stdin)),separators=(',',':')))
