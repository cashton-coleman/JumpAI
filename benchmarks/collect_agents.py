"""Collect native Codex subagent telemetry without exporting prompts or reasoning."""

from datetime import datetime
import json
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[1]
OUT=ROOT/'benchmarks/agent-results/2026-10-16'
PARENT='01a146c8-cac0-78c3-944c-f375281ec2f7'
EXPECTED=json.loads((OUT/'expected.json').read_text())
CONDITIONS=['ordinary_separate','ordinary_chained','jumpai_separate','jumpai_chained']

def write(name,value):
    """Write a JSON artifact with readable indentation; propagate filesystem errors."""
    (OUT/name).write_text(json.dumps(value,indent=2)+'\n')

def parse(path):
    """Extract matching worker identity, counters, timing, and final JSON from native logs."""
    rows = []
    for line in path.open():
        try: rows.append(json.loads(line))
        except ValueError: pass
    meta=next((r['payload'] for r in rows if r.get('type')=='session_meta'),{})
    source=meta.get('source',{})
    if not isinstance(source, dict):
        return None
    source=source.get('subagent',{}).get('thread_spawn',{})
    if source.get('parent_thread_id') != PARENT:
        return None
    name=source.get('agent_path','').split('/')[-1]
    condition=name.rsplit('_',1)[0]
    if condition not in CONDITIONS:
        return None
    contexts=[r['payload'] for r in rows if r.get('type')=='turn_context']
    events=[r for r in rows if r.get('type')=='event_msg']
    starts=[r for r in events if r['payload'].get('type')=='task_started']
    ends=[r for r in events if r['payload'].get('type')=='task_complete']
    usage=[r for r in events if r['payload'].get('type')=='token_count' and r['payload'].get('info')]
    tools=[r for r in rows if r.get('type')=='response_item' and r['payload'].get('type') in ('custom_tool_call','function_call')]
    final = None
    for r in rows:
        p=r.get('payload',{})
        if r.get('type')=='response_item' and p.get('type')=='message' and p.get('role')=='assistant':
            final=''.join(x.get('text','') for x in p.get('content',[]) if x.get('type') in ('output_text','text'))
    completed = bool(ends)
    metrics={'name':name,'condition':condition,'replicate':int(name.rsplit('_',1)[1]),
        'agent_path':source.get('agent_path'),'thread_id':meta.get('id'),'rollout_path':str(path),
        'model':contexts[-1].get('model') if contexts else None,
        'reasoning_effort':contexts[-1].get('effort') if contexts else None,
        'completed':completed,'model_tool_calls':len(tools),
        'model_response_count':len(usage),'final':final}
    if usage:
        u=usage[-1]['payload']['info']['total_token_usage'].copy()
        u['uncached_input_tokens']=u['input_tokens']-u.get('cached_input_tokens',0)
        metrics['usage']=u
    if starts:
        metrics['started_at'] = starts[0]['timestamp']
    if ends:
        end = ends[-1]
        metrics['completed_at']=end['timestamp']
        metrics['wall_seconds']=(datetime.fromisoformat(end['timestamp'].replace('Z','+00:00'))-datetime.fromisoformat(starts[0]['timestamp'].replace('Z','+00:00'))).total_seconds()
        metrics['reported_duration_ms']=end['payload'].get('duration_ms')
        try:
            answer = json.loads(final)
            metrics['correct']=answer==EXPECTED
        except (ValueError,TypeError):metrics['correct']=False
        if not metrics['correct']:
            metrics['validation_error'] = 'Final JSON did not match the independent expected report'
    # Only execution evidence and usage are exported. No system/developer prompts or hidden reasoning.
    trace = []
    for r in rows:
        p=r.get('payload',{})
        if r.get('type')=='response_item' and p.get('type') in ('custom_tool_call','function_call','custom_tool_call_output','function_call_output'):
            trace.append({'timestamp':r['timestamp'],**{k:p[k] for k in ('type','name','call_id','input','arguments','output') if k in p}})
        elif r.get('type')=='event_msg' and p.get('type')=='token_count' and p.get('info'):
            trace.append({'timestamp':r['timestamp'],'type':'token_count','usage':p['info']['total_token_usage'],'last_usage':p['info'].get('last_token_usage')})
    if completed:
        write(f'{name}.execution.json', trace)
    return metrics


def main(output=None, conditions=None):
    """Collect matching native worker measurements and write result and summary files."""
    global OUT, EXPECTED, CONDITIONS
    if output is not None:
        OUT = Path(output)
        EXPECTED=json.loads((OUT/'expected.json').read_text())
    if conditions is not None:
        CONDITIONS = list(conditions)
    runs = []
    for path in (Path.home()/'.codex/sessions/2026/10/16').glob('*.jsonl'):
        result = parse(path)
        if result:
            runs.append(result)
    runs.sort(key=lambda r:r.get('started_at',''))
    write('runs.json',runs)
    for run in runs:
        print(json.dumps({k:run[k] for k in ('name','completed','correct','wall_seconds','model_tool_calls','usage') if k in run}))
    groups = {}
    for condition in CONDITIONS:
        complete=[r for r in runs if r['condition']==condition and r['completed']]
        if not complete:
            continue
        groups[condition]={'completed':len(complete),'correct':sum(r.get('correct',False) for r in complete),
            'wall_seconds':statistics.median(r['wall_seconds'] for r in complete),
            'model_tool_calls':statistics.median(r['model_tool_calls'] for r in complete),
            'usage':{k:statistics.median(r['usage'][k] for r in complete) for k in complete[0]['usage']}}
    write('summary.json',groups)
    return (runs, groups)

if __name__ == '__main__':
    main()
