"""Report the limited direct-invocation experiment from native worker telemetry."""

import hashlib
import json
from pathlib import Path
from collect_agents import main as collect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'benchmarks/agent-results/2026-10-16-recipes'
CONDITIONS = ['ordinary_batch', 'reread_chain', 'recipe_chain']
LABELS = ['Fresh batched code', 'Previous JumpAI workflow path', 'Direct saved JumpAI recipe']


def main():
    """Collect recipe-worker data and write the comparison with retained individual runs."""
    runs, groups = collect(OUT, CONDITIONS)
    if len(runs) != 6 or any(not r['completed'] for r in runs):
        raise SystemExit('Waiting for six completed workers')
    if not all(r['correct'] for r in runs):
        raise SystemExit('Correctness failure; inspect runs.json')
    settings = {(r['model'], r['reasoning_effort']) for r in runs}
    assert len(settings) == 1, settings
    inputs = json.loads((OUT / 'inputs.json').read_text())
    audit = {'input_hashes_match': all(hashlib.sha256((Path(inputs['root']) / p).read_bytes()).hexdigest() == h for p, h in inputs['sha256'].items()), 'usage_sums_match': {}}
    for run in runs:
        trace = json.loads((OUT / (run['name'] + '.execution.json')).read_text())
        counters = [r for r in trace if r['type'] == 'token_count']
        keys = ['input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'total_tokens']
        audit['usage_sums_match'][run['name']] = all(sum(r['last_usage'][k] for r in counters) == run['usage'][k] for k in keys)
    assert audit['input_hashes_match'] and all(audit['usage_sums_match'].values()), audit
    (OUT / 'audit.json').write_text(json.dumps(audit, indent=2) + '\n')
    comparisons = {}
    after = groups['recipe_chain']
    for baseline in ('reread_chain', 'ordinary_batch'):
        before = groups[baseline]
        comparisons[baseline] = {
            'time_reduction_percent': 100 * (1 - after['wall_seconds'] / before['wall_seconds']),
            'total_token_reduction_percent': 100 * (1 - after['usage']['total_tokens'] / before['usage']['total_tokens']),
            'output_token_reduction_percent': 100 * (1 - after['usage']['output_tokens'] / before['usage']['output_tokens']),
        }
    (OUT / 'comparisons.json').write_text(json.dumps(comparisons, indent=2) + '\n')
    model, effort = next(iter(settings))
    lines = ['# Direct cached workflow: limited whole-agent rerun', '',
        f'Six fresh agents, two per condition; `{model}`, effort `{effort}`. All six reports exactly match the independent expected JSON. Inputs are byte-for-byte identical to the earlier experiment.', '',
        '| Condition | Median task seconds | Median total tokens | Input | Cached input subset | Output | Tool calls |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for condition, label in zip(CONDITIONS, LABELS):
        g = groups[condition]
        u = g['usage']
        lines.append(f'| {label} | {g["wall_seconds"]:.2f} | {u["total_tokens"]:,.1f} | {u["input_tokens"]:,.0f} | {u["cached_input_tokens"]:,.0f} | {u["output_tokens"]:,.0f} | {g["model_tool_calls"]:g} |')
    lines += ['', 'Two-observation medians are the midpoint of the two runs. Total tokens include input and output across every model response; cached input is already included, and reasoning is a subset of output. No token estimates or subtraction of standard context are used.', '']
    for baseline, label in [('reread_chain', 'the previous JumpAI path'), ('ordinary_batch', 'fresh batched code')]:
        c = comparisons[baseline]
        lines += [f'Direct replay versus {label}: **{c["time_reduction_percent"]:.1f}% less elapsed time, {c["total_token_reduction_percent"]:.1f}% fewer total tokens, {c["output_token_reduction_percent"]:.1f}% fewer output tokens**.', '']
    lines += ['## Individual observations', '', '| Agent | Seconds | Total tokens | Uncached input | Output | Tool calls |', '| --- | ---: | ---: | ---: | ---: | ---: |']
    for r in runs:
        u = r['usage']
        lines.append(f'| {r["name"]} | {r["wall_seconds"]:.2f} | {u["total_tokens"]:,} | {u["uncached_input_tokens"]:,} | {u["output_tokens"]:,} | {r["model_tool_calls"]} |')
    lines += ['', '## Protocol and interpretation', '',
        '- Workers run sequentially, without parent conversation history, in this order: reread_chain_1, recipe_chain_1, ordinary_batch_1, ordinary_batch_2, recipe_chain_2, reread_chain_2. Model and effort are inherited, then checked from telemetry.',
        '- All workers inventory the same Python tests, check the same local Markdown links, and summarize the same recorded test outcomes. Ordinary workers write their own batched computation code. Both JumpAI conditions execute the same four-stage stored workflow, including the final combiner.',
        '- The previous-path workers receive the original skill from the frozen input snapshot. Their assignment retains the original discovery instructions. Direct workers instead receive the generated recipe card in their assignment: it contains the exact command and applicability, so they do not need to read a skill, resolve a request, or retrieve a contract.',
        '- The common assignment-file read is counted for every condition. The direct condition therefore needs two model-visible tool calls: assignment read and workflow execution. No chaining round trips are hidden or subtracted.',
        '- Elapsed time is native task_started through task_complete; native final total_token_usage includes reasoning/generation, all tool exchanges, and final JSON. Per-response counters sum to these totals, verified in audit.json. Model-visible execution traces preserve tool calls and usage without exporting hidden reasoning.',
        '- Catalog construction, recipe preparation, parent implementation/orchestration and gaps between workers are outside the measured worker tasks. Saving the recipe required one successful workflow invocation before the experiment. Its generated command must be carried forward by the host; this measures repeat-use savings, not first-use setup or amortization.',
        '- Two runs per condition are a deliberately limited pilot, not a statistical significance claim. Service latency and provider prompt-cache state are uncontrolled. The large standard host context is included repeatedly in native token totals; most input is cached. Total-token savings are not equivalent to money savings.',
        '- This fresh matched comparison is primary. The earlier twelve-agent results remain untouched and are not pooled with these runs. Seven focused tests (five new recipe tests and two existing regressions) plus skill validation passed; an exhaustive suite was not run for this change.', '',
        '## Evidence', '',
        '- [Native run metrics](runs.json), [median summaries](summary.json), [comparisons](comparisons.json), [usage and input audit](audit.json)',
        '- [Recipe card](recipe-card.json), [input provenance](inputs.json), [expected report](expected.json)',
        '- Per-agent `.prompt.txt` and `.execution.json` files record the task instructions, execution and native usage counters.',
        '- Recollect with `python3 benchmarks/report_recipes.py`. `prepare_recipe_agents.py` prepares the six assignments and recipe using the original frozen inputs; worker dispatch uses native subagents.', '']
    (OUT / 'report.md').write_text('\n'.join(lines))
    print(json.dumps(comparisons, indent=2))


if __name__ == '__main__':
    main()
