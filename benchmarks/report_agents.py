"""Produce the whole-agent report from native telemetry collected by collect_agents."""

import json
from pathlib import Path
import statistics
from collect_agents import main as collect

OUT=Path(__file__).resolve().parents[1]/'benchmarks/agent-results/2026-10-16'
LABELS={'ordinary_separate':'Ordinary code, separate calls','ordinary_chained':'Ordinary code, batched script',
        'jumpai_separate':'JumpAI, separate calls','jumpai_chained':'JumpAI, stored workflow'}


def pct(after, before):
    """Return the percentage reduction from the reference value to the measured value."""
    return 100 * (before - after) / before


def main():
    """Collect completed worker data and write the comparison report and percentages."""
    runs, groups = collect()
    if len(runs)!=12 or any(not r['completed'] for r in runs):
        raise SystemExit('Report requires all 12 completed runs; partial runs remain in runs.json')
    if any(not r['correct'] for r in runs):
        raise SystemExit('A correctness failure must be reported and investigated before summarizing successful savings')
    models={(r['model'],r['reasoning_effort']) for r in runs}
    if len(models) != 1:
        raise SystemExit(f'Model/settings mismatch: {models}')
    model, effort = next(iter(models))
    comparisons = {}
    for name,after,before in [
        ('ordinary_batching','ordinary_chained','ordinary_separate'),
        ('jumpai_workflow_chaining','jumpai_chained','jumpai_separate'),
        ('jumpai_workflow_vs_ordinary_separate','jumpai_chained','ordinary_separate'),
        ('jumpai_workflow_vs_ordinary_batched','jumpai_chained','ordinary_chained')]:
        a, b = (groups[after], groups[before])
        comparisons[name]={'time_reduction_percent':pct(a['wall_seconds'],b['wall_seconds']),
            'total_token_reduction_percent':pct(a['usage']['total_tokens'],b['usage']['total_tokens']),
            'output_token_reduction_percent':pct(a['usage']['output_tokens'],b['usage']['output_tokens']),
            'uncached_input_reduction_percent':pct(a['usage']['uncached_input_tokens'],b['usage']['uncached_input_tokens'])}
    (OUT/'comparisons.json').write_text(json.dumps(comparisons,indent=2)+'\n')
    lines=['# JumpAI: whole-agent reuse and chaining experiment','',
        f'**12 fresh agents; three runs per condition; model `{model}`, reasoning effort `{effort}`. All 12 final reports match the expected JSON.**','',
        'Each agent completes the same three tasks: inventory the 27 Python tests, check local links across three Markdown documents, and summarize a recorded real test run. Every run starts without parent conversation history. Native Codex counters measure actual model usage; no tokenizer proxy is used.','',
        '## Median end-to-end worker results','',
        '| Condition | Task time (s) | Total tokens | Input tokens | Cached input | Output tokens | Reasoning subset | Model-visible tool calls |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for condition,label in LABELS.items():
        r = groups[condition]
        u = r['usage']
        lines.append(f'| {label} | {r["wall_seconds"]:.2f} | {u["total_tokens"]:,.0f} | {u["input_tokens"]:,.0f} | {u["cached_input_tokens"]:,.0f} | {u["output_tokens"]:,.0f} | {u["reasoning_output_tokens"]:,.0f} | {r["model_tool_calls"]:g} |')
    lines += ['', 'Each column is its own median across three runs; median components need not sum to the median total. Total tokens already include cached input and reasoning output. Most input is cached context, so total-token reduction is not a dollar-cost estimate.', '',
        '## Comparisons of medians','',
        '| Comparison | Task-time reduction | Total-token reduction | Output-token reduction |',
        '| --- | ---: | ---: | ---: |']
    for name,label in [('ordinary_batching','Ordinary batching vs ordinary separate'),('jumpai_workflow_chaining','JumpAI workflow vs JumpAI separate'),
        ('jumpai_workflow_vs_ordinary_separate','JumpAI workflow vs ordinary separate'),('jumpai_workflow_vs_ordinary_batched','JumpAI workflow vs ordinary batched')]:
        c = comparisons[name]
        lines.append(f'| {label} | {c["time_reduction_percent"]:.1f}% | {c["total_token_reduction_percent"]:.1f}% | {c["output_token_reduction_percent"]:.1f}% |')
    lines += ['', 'Negative reductions mean an increase. The ordinary batched condition is the stronger baseline: it permits one combined Python script, with no manufactured intermediate tool traffic.', '',
        '## All measured runs','',
        '| Agent | Time (s) | Total tokens | Uncached input | Cached input | Output | Reasoning subset | Tool calls | Correct |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |']
    for r in runs:
        u=r['usage']
        lines.append(f'| {r["name"]} | {r["wall_seconds"]:.2f} | {u["total_tokens"]:,} | {u["uncached_input_tokens"]:,} | {u["cached_input_tokens"]:,} | {u["output_tokens"]:,} | {u["reasoning_output_tokens"]:,} | {r["model_tool_calls"]} | yes |')
    lines += ['', '## What is measured','',
        '- Time runs from each native worker task_started to task_complete, covering reasoning/generation, skill and contract reads, tools, execution, and final JSON.',
        '- Usage is the final native total_token_usage for that fresh worker, including all model responses in the task. Repeated context counts repeatedly; cached context is explicitly reported.',
        '- Every worker begins by reading its assignment file. This common benchmark-wrapper tool call is included, with no subtraction.',
        '- The catalog is populated, verified and cached before the workers start. Each JumpAI agent still pays for reading the entry skill and any contract it requests.',
        '- Stored chaining executes inventory, link checking, and test summarization, then passes their results to reports.combine. These four program invocations need no intermediate model decisions.',
        '- Ordinary agents generate their own computation code. Ordinary batching allows a single fresh script. All conditions use one input snapshot and an identical output contract.',
        '- Parent orchestration, catalog/program authoring, registration, fixture verification, and gaps between workers are excluded. These are not total experiment costs or first-use break-even numbers.', '',
        '## Limits','',
        'This is a three-replicate pilot on small known tasks, not a statistical significance claim. Agents run sequentially in a fixed, interleaved order. Service latency and provider prompt-cache state are uncontrolled. All agents receive the same large standard host instructions/tool metadata, even with no parent history; reducing model exchanges therefore changes repeated input-token usage substantially. Prompt wording intentionally enforces separate computation calls in the separate conditions. Neither model usage nor savings should be extrapolated to unrelated workloads.', '',
        'The actual output-token count captures generated tool commands, prose/final JSON and reported reasoning. It does not isolate code-writing tokens. A zero reasoning subset means zero was reported in that counter, not an independent claim about internal computation.', '',
        '## Evidence','',
        '- [Protocol and run order](protocol.md)',
        '- [Native run metrics](runs.json)',
        '- [Median summaries](summary.json)',
        '- [Comparison calculations](comparisons.json)',
        '- [Input provenance](inputs.json)',
        '- [Correctness reference](expected.json)',
        '- Individual `.prompt.txt` and `.execution.json` files preserve assignments, tool commands/results, and usage counters. Hidden reasoning and unrelated system prompts are not exported.', '',
        'The native raw rollout paths and worker IDs remain in runs.json for local audit. Codex officially documents token-usage events for agent evaluations in [Testing Agent Skills Systematically with Evals](https://developers.openai.com/blog/eval-skills).', '']
    (OUT/'report.md').write_text('\n'.join(lines))
    print('\n'.join(lines[:16]))

if __name__ == '__main__':
    main()
