"""Render complete native measurements with independent audit outcomes visible."""

import json
from pathlib import Path


def render(args):
    """Validate completed measurements and write a report retaining failed audit outcomes."""
    output=Path(args['output_dir']).resolve()
    inputs=json.loads((output/'inputs.json').read_text())
    runs=json.loads((output/'runs.json').read_text())
    groups=json.loads((output/'summary.json').read_text())
    audit=json.loads((output/'audit.json').read_text())
    names=set(inputs['workers'])
    if {r['name'] for r in runs} != names or len(runs) != len(names):
        raise ValueError('Report requires exactly the expected workers')
    if not all((r['completed'] and r['counter_sums_match'] for r in runs)):
        raise ValueError('Report requires completed workers and valid native counters')
    if set(audit['workers']) != names:
        raise ValueError('Audit worker inventory mismatch')
    final_passed=sum(bool(r['correct']) for r in runs)
    tests_passed=sum(bool(audit['workers'][r['name']]['selected_tests']['passed']) for r in runs)
    restored=sum(bool(audit['workers'][r['name']]['exact_original_source_restored']) for r in runs)
    settings={(r['model'],r['reasoning_effort']) for r in runs}
    if len(settings) != 1:
        raise ValueError('Worker model/settings differ')
    conditions = args['conditions']
    reference = args['reference_condition']
    if reference not in conditions or set(conditions) != set(groups):
        raise ValueError('Conditions/reference mismatch')
    comparisons = {}
    for before in conditions:
        if before == reference:
            continue
        left, right = (groups[before], groups[reference])
        comparisons[reference+'_vs_'+before]={
            'total_token_reduction_percent':100*(1-right['usage']['total_tokens']/left['usage']['total_tokens']),
            'model_exchange_reduction_percent':100*(1-right['model_exchanges']/left['model_exchanges']),
            'time_reduction_percent':100*(1-right['wall_seconds']/left['wall_seconds']),
            'output_token_reduction_percent':100*(1-right['usage']['output_tokens']/left['usage']['output_tokens'])}
    (output/'comparisons.json').write_text(json.dumps(comparisons,indent=2)+'\n')
    labels = args['labels']
    model, effort = next(iter(settings))
    # A human can classify mixed read/write candidates without editing measured traces.
    review_path=output/'source-read-review.json'
    reviews = json.loads(review_path.read_text()) if review_path.exists() else {}
    if not set(reviews) <= names:
        raise ValueError('Source-read review names unknown worker')
    for name,value in reviews.items():
        if type(value.get('extra_source_read_calls')) is not int or value['extra_source_read_calls'] < 0 or (not isinstance(value.get('note'), str)):
            raise ValueError('Invalid source-read review entry')
    lines=['# '+args['title'],'',args['context'],'',f'{len(runs)} fresh workers; model `{model}`, reasoning effort `{effort}`. **{final_passed}/{len(runs)} final reports match, {tests_passed}/{len(runs)} selected-test audits pass, and {restored}/{len(runs)} exactly restore the frozen healthy source.** All measurements remain included. Overall independent audit: {"passed" if audit["all_passed"] else "failed; see per-worker outcomes"}.','', '## Median whole-task measurements','', '| Condition | Total tokens | Model exchanges | Tool calls | Seconds | Output tokens | Cached input subset |','| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for condition in conditions:
        g = groups[condition]
        u = g['usage']
        lines.append(f'| {labels[condition]} | {u["total_tokens"]:,.1f} | {g["model_exchanges"]:g} | {g["model_tool_calls"]:g} | {g["wall_seconds"]:.2f} | {u["output_tokens"]:,.1f} | {u["cached_input_tokens"]:,.0f} |')
    lines+=['','## Comparisons','']
    for key,c in comparisons.items():
        before=key.split('_vs_',1)[1]
        lines.append(f'{labels[reference]} versus {labels[before]}: **{c["total_token_reduction_percent"]:.1f}% fewer total tokens**, **{c["model_exchange_reduction_percent"]:.1f}% fewer model exchanges**, and **{c["time_reduction_percent"]:.1f}% less time**. Output-token reduction: {c["output_token_reduction_percent"]:.1f}%. Negative reductions mean an increase.')
    lines+=['','## Individual runs','','| Agent | Total tokens | Exchanges | Tool calls | Seconds | Source-read candidates | Reviewed extra source reads |','| --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in runs:
        reviewed=reviews.get(r['name'],{}).get('extra_source_read_calls','unreviewed')
        lines.append(f'| {r["name"]} | {r["usage"]["total_tokens"]:,} | {r["model_response_count"]} | {r["model_tool_calls"]} | {r["wall_seconds"]:.2f} | {len(r["source_read_candidates"])} | {reviewed} |')
    lines+=['','Source-read candidates come from a command heuristic, not a measurement of intent. Mixed read/write calls can match. Reviewed counts, when present, are separately supplied evaluator annotations in source-read-review.json; original commands remain in execution traces. No extra source read is prohibited.']
    lines+=['','## Independent correctness audit','','| Agent | Final report | Selected tests | Exact baseline restored | Mismatched frozen files |','| --- | --- | --- | --- | --- |']
    for r in runs:
        a=audit['workers'][r['name']]
        mismatches=', '.join(a.get('mismatched_files',[])) or 'none'
        lines.append(f'| {r["name"]} | {"pass" if r["correct"] else "FAIL"} | {"pass" if a["selected_tests"]["passed"] else "FAIL"} | {"yes" if a["exact_original_source_restored"] else "NO"} | {mismatches} |')
    lines+=['','Selected-test success and exact source restoration are separate checks. Limited tests can pass for a repair that differs from the healthy baseline; source mismatch does not by itself establish every behavior that differs. Failed audit outcomes remain visible and their timing/tokens stay in every aggregate.']
    if reviews:
        lines+=['','Review notes:','']
        for name, value in reviews.items():
            lines.append(f"- `{name}`: {value['note']}")
    lines+=['','## Measurement boundaries','',
        '- Native final whole-worker counters measure input, cached input, output and total tokens. Cached input is included in input/total; reasoning is a subset of output. Per-response usage sums match final counters.',
        '- Model exchanges count native response/usage events, including the final answer. Tool calls count model-visible outer calls, not each command or workflow stage within them.',
        '- Time covers native task start through completion. Native timestamps and dispatch order appear in runs.json. Setup, development, verification, recipe seeding, orchestration and evaluator audits are excluded.',
        '- This measures repeat use, not first-use amortization or dollar costs. Small samples, prompt-cache state, service latency and agent choices limit generalization. Fractional medians are aggregate statistics; individual token/exchange counts are integers.']
    lines+=['','## Experiment details','']+['- '+note for note in args['notes']]
    lines+=['','## Evidence','',
        '- [Protocol](protocol.md), [native usage](runs.json), [medians](summary.json), [comparisons](comparisons.json)',
        '- [Independent source and selected-test audit](audit.json), [counter audit](usage-audit.json)',
        '- Per-worker `.prompt.txt` and `.execution.json` files retain assignments and execution evidence. Exported traces exclude hidden reasoning and system/developer prompts.', '']
    (output/'report.md').write_text('\n'.join(lines))
    return {'ready':True,'all_passed':audit['all_passed'],'report':str(output/'report.md'),'comparisons':comparisons,'summary':str(output/'summary.json'),'source_read_reviewed':len(reviews)}
