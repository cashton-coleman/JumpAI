"""Export observable adoption evidence from native root/descendant agent sessions."""

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sys


def write(path, value):
    """Write a JSON artifact with readable indentation; propagate filesystem errors."""
    path.write_text(json.dumps(value, indent=2) + '\n')


def audit(args):
    """Follow native agent ancestry, export observable traces, and total root-plus-child usage."""
    output = Path(args['output_dir']).resolve()
    output.mkdir(parents=True, exist_ok=True)
    sessions = {}
    for path in sorted(Path(args['sessions_dir']).glob('*.jsonl')):
        rows = []
        with path.open() as stream:
            for line in stream:
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    # Live logs can contain an unfinished record; readiness is checked later.
                    continue
        meta = next((r['payload'] for r in rows if r.get('type') == 'session_meta'), {})
        source = meta.get('source', {})
        if not isinstance(source, dict):
            continue
        spawned = source.get('subagent', {}).get('thread_spawn', {})
        if spawned:
            sessions[meta['id']] = (path, rows, spawned)
    # Follow parent IDs to include descendants without unrelated agents.
    selected = {key: spawned['agent_path'] for key, (_, _, spawned) in sessions.items()
                if spawned.get('parent_thread_id') == args['parent_thread_id'] and spawned.get('agent_path') in args['root_agent_paths']}
    roots_found = set(selected.values())
    while True:
        children = {key: selected[spawned['parent_thread_id']] for key, (_, _, spawned) in sessions.items()
                    if key not in selected and spawned.get('parent_thread_id') in selected}
        if not children:
            break
        selected.update(children)
    # Export observable execution evidence, keeping private reasoning out of traces.
    results = []
    for key, root in selected.items():
        path, rows, spawned = sessions[key]
        events = [r for r in rows if r.get('type') == 'event_msg']
        starts = [r for r in events if r['payload'].get('type') == 'task_started']
        ends = [r for r in events if r['payload'].get('type') == 'task_complete']
        counts = [r for r in events if r['payload'].get('type') == 'token_count' and r['payload'].get('info')]
        calls = [r for r in rows if r.get('type') == 'response_item' and r['payload'].get('type') in ('custom_tool_call', 'function_call')]
        context = [r['payload'] for r in rows if r.get('type') == 'turn_context']
        instruction_presence = False
        skill_presence = False
        for row in rows:
            p = row.get('payload', {})
            if row.get('type') == 'response_item' and p.get('type') == 'message' and p.get('role') in ('user', 'developer', 'system'):
                # Report only presence flags; never export host prompts or private reasoning.
                text = json.dumps(p.get('content', []))
                instruction_presence |= '<!-- jumpai:begin -->' in text
                skill_presence |= ('jumpai/SKILL.md' in text and 'Available skills' in text)
        invocation_candidates = []
        delegations = []
        for row in calls:
            p = row['payload']
            command = str(p.get('input', p.get('arguments', '')))
            if re.search(r'jumpai\.py|python\S* -m jumpai|from jumpai\.runner', command):
                invocation_candidates.append({'call_id': p.get('call_id'), 'command': command})
            if 'spawn_agent' in p.get('name', ''):
                delegations.append({'call_id': p.get('call_id'), 'arguments': command})
        trace = []
        for row in rows:
            p = row.get('payload', {})
            if row.get('type') == 'response_item' and p.get('type') in ('custom_tool_call', 'function_call', 'custom_tool_call_output', 'function_call_output'):
                trace.append({'timestamp': row['timestamp'], **{k: p[k] for k in ('type', 'name', 'call_id', 'input', 'arguments', 'output') if k in p}})
        trace_path = output / (hashlib.sha256(key.encode()).hexdigest()[:16] + '.trace.json')
        write(trace_path, trace)
        item = {'agent_path': spawned['agent_path'], 'thread_id': key, 'parent_thread_id': spawned['parent_thread_id'], 'root_agent_path': root,
                'role': 'root' if spawned['agent_path'] == root else 'descendant', 'completed': bool(ends),
                'global_guidance_observed_in_log': instruction_presence, 'skill_catalog_observed_in_log': skill_presence,
                'invocation_candidates': invocation_candidates, 'delegations': delegations, 'tool_calls': len(calls),
                'model_exchanges': len(counts), 'model': context[-1].get('model') if context else None,
                'trace': str(trace_path), 'native_log': str(path)}
        if counts:
            usage = counts[-1]['payload']['info']['total_token_usage']
            item['usage'] = usage
            keys = ['input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'total_tokens']
            item['counter_sums_match'] = all(sum((c['payload']['info'].get('last_token_usage') or {}).get(k, 0) for c in counts) == usage.get(k, 0) for k in keys)
        if starts and ends:
            item['wall_seconds'] = (datetime.fromisoformat(ends[-1]['timestamp'].replace('Z', '+00:00')) - datetime.fromisoformat(starts[0]['timestamp'].replace('Z', '+00:00'))).total_seconds()
        results.append(item)
    results.sort(key=lambda r: r['agent_path'])
    write(output / 'adoption-evidence.json', results)
    ready = roots_found == set(args['root_agent_paths']) and all(r['completed'] for r in results)
    aggregates = {root: {'agents': sum(r['root_agent_path'] == root for r in results),
                         'total_tokens': sum(r.get('usage', {}).get('total_tokens', 0) for r in results if r['root_agent_path'] == root),
                         'model_exchanges': sum(r['model_exchanges'] for r in results if r['root_agent_path'] == root)} for root in args['root_agent_paths']}
    return {'ready': ready, 'agents': len(results), 'evidence': str(output / 'adoption-evidence.json'), 'totals_including_descendants': aggregates,
            'note': 'Invocation candidates require execution-trace review; absence of instruction text in logs does not prove absence from model context.'}


if __name__ == '__main__':
    print(json.dumps(audit(json.load(sys.stdin))))
