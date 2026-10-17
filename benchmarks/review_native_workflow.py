"""Validate paired workflow reports and summarize actual whole-agent telemetry."""

import hashlib
import json
from pathlib import Path
import sqlite3
import sys


def main():
    """Require correct reports, unchanged inputs, and native receipts before comparing costs."""
    root = Path(json.load(sys.stdin)['output_dir'])
    expected = json.loads((root / 'expected.json').read_text())
    hashes = json.loads((root / 'input-hashes.json').read_text())
    for name, digest in hashes.items():
        if hashlib.sha256((root / 'inputs' / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f'Input changed: {name}')
    receipts = {}
    normalized_fields = []
    for condition in ('recipe', 'ordinary'):
        receipt = json.loads((root / condition / 'result.json').read_text())
        # The prompt specified total byte/line values without fixing these two key names.
        inventory = receipt['output']['inventory']
        for canonical in ('bytes', 'lines'):
            alias = 'total_' + canonical
            if canonical not in inventory and alias in inventory:
                inventory[canonical] = inventory.pop(alias)
                normalized_fields.append(f'{condition}.inventory.{alias} -> {canonical}')
        if receipt['output'] != expected:
            raise ValueError(f'{condition} report does not match the reference')
        receipts[condition] = receipt
    native = receipts['recipe'].get('native_steps', {})
    if len(native) != 4 or any(step['status'] != 'ready' for step in native.values()):
        raise ValueError('Measured workflow did not use C++ for all four steps')
    warm = json.loads((root / 'warm-receipt.json').read_text())['native_steps']
    if native != warm:
        raise ValueError('Native build evidence changed after warmup')

    # Native logs supply token counters and start/end times, never estimates from text.
    agents = json.loads((root / 'adoption-evidence.json').read_text())
    metrics = {}
    for condition in ('recipe', 'ordinary'):
        matches = [agent for agent in agents if agent['agent_path'] == f'/root/native_workflow_{condition}_1']
        if len(matches) != 1 or not matches[0]['completed'] or not matches[0]['counter_sums_match']:
            raise ValueError(f'Missing or inconsistent telemetry: {condition}')
        agent = matches[0]
        metrics[condition] = {key: agent[key] for key in ('wall_seconds', 'model_exchanges', 'tool_calls', 'model', 'usage')}
    if metrics['recipe']['model'] != metrics['ordinary']['model']:
        raise ValueError('Agents used different models')

    # Confirm the saved recipe ran exactly once and chained exactly four successful children.
    with sqlite3.connect(root / 'catalog/catalog.sqlite3') as database:
        rows = database.execute('SELECT target_id,parent_id,duration_ms,success FROM runs ORDER BY id').fetchall()
    if len(rows) != 5 or any(row[3] != 1 for row in rows):
        raise ValueError('Expected one successful workflow and four successful child runs')
    result = {'outputs_match': True, 'normalized_fields': normalized_fields,
              'inputs_unchanged': True, 'native_steps_ready': len(native),
              'files': expected['inventory']['files_scanned'], 'source_bytes': expected['inventory']['bytes'],
              'recorded_tests': expected['tests']['tests'], 'agents': metrics,
              'workflow_runs': rows, 'setup': json.loads((root / 'setup.json').read_text()),
              'limits': 'One fresh agent per condition; warm recipe reuse excludes one-time authoring, compilation, preparation and parent audit costs. Ordinary control may choose its language and batch work.'}
    result['savings_percent'] = {
        'wall_time': 100 * (1 - metrics['recipe']['wall_seconds'] / metrics['ordinary']['wall_seconds']),
        'total_tokens': 100 * (1 - metrics['recipe']['usage']['total_tokens'] / metrics['ordinary']['usage']['total_tokens']),
        'output_tokens': 100 * (1 - metrics['recipe']['usage']['output_tokens'] / metrics['ordinary']['usage']['output_tokens']),
    }
    (root / 'review.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
