"""Report the limited conditional-workflow benchmark from native agent usage."""

import json
from pathlib import Path
import statistics

from collect_agents import main as collect

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'benchmarks/agent-results/2026-10-16-triage'
CONDITIONS = ['ordinary_triage', 'jumpai_triage']


def main():
    """Collect regression-worker data and write measurements alongside the source audit."""
    runs, groups = collect(OUT, CONDITIONS)
    if len(runs) != 4 or not all(r['completed'] and r['correct'] for r in runs):
        raise SystemExit('Report requires four completed, correct workers')
    source_audit = json.loads((OUT / 'audit.json').read_text())
    if not source_audit['all_passed']:
        raise SystemExit('Source/test audit failed')
    settings = {(r['model'], r['reasoning_effort']) for r in runs}
    assert len(settings) == 1, settings
    usage_audit = {}
    for run in runs:
        trace = json.loads((OUT / (run['name'] + '.execution.json')).read_text())
        counters = [r for r in trace if r['type'] == 'token_count']
        keys = ['input_tokens', 'cached_input_tokens', 'output_tokens', 'reasoning_output_tokens', 'total_tokens']
        usage_audit[run['name']] = {
            'response_count': len(counters),
            'counter_sums_match': all(sum(r['last_usage'][key] for r in counters) == run['usage'][key] for key in keys),
        }
        assert usage_audit[run['name']]['counter_sums_match']
    (OUT / 'usage-audit.json').write_text(json.dumps(usage_audit, indent=2) + '\n')
    for condition in CONDITIONS:
        group = [r for r in runs if r['condition'] == condition]
        groups[condition]['model_exchanges'] = statistics.median(r['model_response_count'] for r in group)
    (OUT / 'summary.json').write_text(json.dumps(groups, indent=2) + '\n')
    before, after = [groups[c] for c in CONDITIONS]
    comparisons = {
        'total_token_reduction_percent': 100 * (1 - after['usage']['total_tokens'] / before['usage']['total_tokens']),
        'model_exchange_reduction_percent': 100 * (1 - after['model_exchanges'] / before['model_exchanges']),
        'time_reduction_percent': 100 * (1 - after['wall_seconds'] / before['wall_seconds']),
        'output_token_reduction_percent': 100 * (1 - after['usage']['output_tokens'] / before['usage']['output_tokens']),
        'uncached_input_reduction_percent': 100 * (1 - after['usage']['uncached_input_tokens'] / before['usage']['uncached_input_tokens']),
    }
    (OUT / 'comparisons.json').write_text(json.dumps(comparisons, indent=2) + '\n')
    model, effort = next(iter(settings))
    lines = ['# Conditional, resumable workflow: limited agent comparison', '',
        f'Four fresh agents, two per condition; model `{model}`, reasoning effort `{effort}`. All four repaired the same seeded regression and returned correct reports. Each repaired source exactly matches the healthy frozen baseline; all other frozen files remain unchanged. Independent full-suite audits passed 32 tests per repaired snapshot.', '',
        '## Median whole-task measurements', '',
        '| Condition | Total tokens | Model exchanges | Model-visible tool calls | Seconds | Output tokens | Uncached input | Cached input subset |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for condition, label in zip(CONDITIONS, ['Ordinary agent, batching allowed', 'JumpAI conditional workflow']):
        g = groups[condition]
        u = g['usage']
        lines.append(f'| {label} | {u["total_tokens"]:,.1f} | {g["model_exchanges"]:g} | {g["model_tool_calls"]:g} | {g["wall_seconds"]:.2f} | {u["output_tokens"]:,.1f} | {u["uncached_input_tokens"]:,.1f} | {u["cached_input_tokens"]:,.0f} |')
    lines += ['',
        f'JumpAI used **{comparisons["total_token_reduction_percent"]:.1f}% fewer total tokens**, **{comparisons["model_exchange_reduction_percent"]:.1f}% fewer model exchanges**, and **{comparisons["time_reduction_percent"]:.1f}% less time**. Generated output fell {comparisons["output_token_reduction_percent"]:.1f}%; uncached input fell {comparisons["uncached_input_reduction_percent"]:.1f}%.', '',
        'With two observations, each median is the midpoint of the two values. Fractional token/exchange counts are aggregate statistics; each actual run has integer counts. These are native usage counters, not tokenizer estimates. Cached input is already included in input/total tokens; reasoning is a subset of output, not an extra quantity.', '',
        '## Individual runs', '',
        '| Agent | Total tokens | Exchanges | Tool calls | Seconds | Correct repair/report |',
        '| --- | ---: | ---: | ---: | ---: | --- |']
    for run in runs:
        lines.append(f'| {run["name"]} | {run["usage"]["total_tokens"]:,} | {run["model_response_count"]} | {run["model_tool_calls"]} | {run["wall_seconds"]:.2f} | yes |')
    lines += ['', '## Task and execution', '',
        'The task is a controlled regression repair in an isolated copy of the actual JumpAI repository. Preparation removed case folding from query_key. Workers were told the allowed source file and three existing tests, but were not told the defect or repair. Initially two tests pass and one fails; after the model-selected repair all three pass. This is a seeded regression, not a naturally occurring production incident.', '',
        'The stored workflow runs the selected tests, conditionally collects AST-based source/failure evidence, and pauses for a model-selected exact replacement. Resume checks the decision schema and evidence digest, applies the patch once, reruns the tests and generates a compact report. If initial tests pass, the evidence, decision, patch and rerun branches are skipped. Both healthy and failing branches were verified before measurement.', '',
        'Reusable programs and workflow manifests contain no known diagnosis or repair. They retain intermediate data in SQLite and a local evidence file. Both JumpAI workers nevertheless chose to read an additional 85–90 lines of source before deciding, even though the relevant function was in checkpoint context. Those calls and tokens remain fully counted. No rerun or exclusion was used to obtain a better result.', '',
        'Ordinary agents could batch commands freely. One used three model-visible tool calls; the other used four. Each JumpAI agent used three. This experiment therefore shows a modest reduction in model exchanges, rather than eliminating most reasoning steps; narrower evidence also reduces repeated context volume.', '',
        '## Measurement boundaries', '',
        '- Workers run sequentially in order: ordinary_triage_1, jumpai_triage_1, jumpai_triage_2, ordinary_triage_2. Each uses fork_turns=none and inherited model/settings, checked from telemetry.',
        '- The full saved assignment is dispatched directly as the worker message. There is no benchmark-wrapper file read in any condition. JumpAI assignments include a prepared recipe and resume syntax; ordinary assignments allow arbitrary efficient shell/Python batching.',
        '- A model exchange is one native model response/usage event, including the final answer. Model-visible tool calls count outer exec/function calls, not every shell command or program invoked within them. Running multiple commands within one exec does not count as additional model exchanges.',
        '- Time covers native task_started through task_complete. Usage is final whole-worker total_token_usage. Per-response counter sums match final totals in usage-audit.json.',
        '- Source collection, workflow/program authoring, fixture verification, recipe seeding, parent orchestration and post-run full-suite audits are excluded. This measures repeat use of prepared mechanics, not first-use amortization or total experiment cost.',
        '- The preparation baseline was frozen before the new workflow engine edits. Workers repair that frozen earlier application; the external prepared JumpAI tool runs the new workflow engine. This isolates the same defect/test workload for both conditions.',
        '- Two runs per condition are a small pilot. Service latency, prompt-cache state, agent inspection choices, and context reuse are uncontrolled. Total-token savings are not dollar-cost savings. Do not extrapolate these percentages to other tasks.', '',
        '## Implementation validation', '',
        'The current repository suite passed 41 tests, including nine focused checkpoint tests: process restart/persistence, skipped decisions and fallback values, invalid/stale/scope/availability checks, concurrent resume, failed continuation handling, decision fixtures, CLI recipe replay, immediate-completion recipe saving, and context limits. The skill validator passed. The independent repaired-snapshot audit ran 32 frozen tests in each of four separate audit copies, with required example fixtures hashed and recorded.', '',
        'After measurement, a bounded review found that immediately completed conditional runs could not save recipes through the CLI. The run --resumable --save-recipe path was fixed and covered by the additional regression test. The measured jump/resume paths were unchanged. benchmark-runtime-hashes.json preserves the implementation and reusable-program hashes used during measurement.', '',
        '## Evidence', '',
        '- [Protocol](protocol.md), [native worker metrics](runs.json), [summaries](summary.json), [comparisons](comparisons.json)',
        '- [Source and full-suite audit](audit.json), [usage counter audit](usage-audit.json), [baseline hashes](baseline-hashes.json)',
        '- [Expected final report](expected.json), per-agent `.prompt.txt` assignments and `.execution.json` traces',
        '- Native raw rollout paths remain in runs.json. Exported traces contain tool calls/results and usage, not hidden reasoning.', '',
        'Recollect with `python3 benchmarks/report_triage.py`. Run `python3 benchmarks/audit_triage.py` only after all four native workers have completed. Preparation and seeded verification are in `benchmarks/prepare_triage_agents.py`; measured dispatch uses the saved prompts directly through native subagents.', '']
    (OUT / 'report.md').write_text('\n'.join(lines))
    print(json.dumps(comparisons, indent=2))


if __name__ == '__main__':
    main()
