# What this benchmark can and cannot establish

This document describes the original command-payload microbenchmark. For the later comparison using separate agents, actual token counters, and a warmed C++ workflow, see the [developer guide's measured comparison](../DEVELOPER_GUIDE.md#measured-workflow-comparison) and its [retained review receipt](native-workflow-result.json). The two experiments measure different boundaries.

The question is whether reusing a cataloged program saves work on ordinary agent tasks. This first experiment measures local command execution and text crossing a command interface. It does not run separate LLM sessions and does not measure total turn latency or model billing. Keep that distinction attached to every result.

## Workloads

1. Inventory test functions and methods from the actual JumpAI test source without executing it.
2. Audit inline local links in JumpAI's README and two skill documents. This deliberately limited Markdown scanner skips fenced code, external URLs, and anchors; it does not validate reference links or full Markdown syntax.
3. Summarize recorded outcomes and timings from a real test run. Running the tests is shared input preparation, not part of the summary task.

The same program source is used in all three paths:

- **Fresh code:** pass the complete Python source to `python -c` each time, with JSON input. This models repeatedly supplying an ad hoc script. The harness replays that source; it does not regenerate it with an LLM on every trial.
- **Saved script:** invoke the existing file with JSON input. This controls for ordinary script reuse without a catalog.
- **JumpAI cached:** invoke the CLI using a persisted request resolution and JSON input. It runs the same source from its immutable bundle and returns its usual metadata envelope.

Each uses one shell invocation. We do not inflate the baseline by reading files into the model, splitting it into unnecessary steps, or printing verbose intermediate output. No tool-call reduction is claimed for these already batched tasks.

## Timing

Measure the full shell subprocess with `perf_counter_ns`, including Python startup, catalog lookup, schema checks, integrity hashing, child execution, SQLite run history, and result serialization where applicable. Do not use JumpAI's inner `duration_ms` for the comparison: that field omits some CLI overhead.

Use 30 measured runs per task/path, after one warm-up per path, and randomize both task and path order within each round with a saved seed. Report medians, retain min/p95/max and every sample. This is a warm-cache local microbenchmark on a shared machine, not a cold-start or cross-machine benchmark. Reject nonzero exit status, incorrect output, or any expected cache hit that misses.

## Token counts

Use `tiktoken` with `o200k_base` to count the exact serialized `{cmd, workdir}` arguments plus returned stdout/stderr text. Record command and result counts separately. The model's exact current tokenizer is not asserted; these are reproducible counts under the named encoding.

Excluded: model reasoning, system/skill/conversation context, final explanations, outer tool protocol framing, transport metadata, model prompt caching, and billed-token counters. These measurements establish the difference in supplied code and returned text, not the actual end-to-end model token bill. Do not convert payload savings into seconds using an assumed generation speed.

## Setup and amortization

Use a fresh catalog for each benchmark dataset. Register and verify all three programs before creating bindings, because the current cache globally invalidates on catalog changes. Record first discovery, contract inspection, and execution with `--remember` separately from warmed repeated use.

The optional token break-even calculation counts source, manifest, verification fixtures and setup command/output payloads once, then adds cached reuse. It is an artifact-text proxy. It excludes actual authoring/debugging token use and skill loading, and therefore is not a measured end-to-end break-even claim. Ordinary saved scripts have their own much smaller setup requirements and are reported as a separate control.

Small, hand-authored fixtures verify correctness independently (including failed tests and missing links). The measured workloads use real repository inputs, not these fixtures. Save SHA-256 input hashes, raw test outcomes, raw measurement samples, and full command transcripts for auditability. Results contain local absolute paths but no private test output or environment values.

## Reproduce

Install the benchmark-only `tiktoken` dependency in an environment of your choice. The JumpAI runtime remains standard-library-only.

```bash
python3 benchmarks/run.py --output benchmarks/results/my-unique-run --rounds 30
```

The output directory must not exist. The harness keeps its catalog under `.jumpai/benchmarks/<output-directory-name>` and does not modify existing program versions or cache bindings in the main catalog.

Generated output under `benchmarks/results` and `benchmarks/agent-results` is ignored and may be deleted. `benchmarks/fixtures/test-outcomes.json` retains an earlier real test run solely as an input dataset, not as evidence about the current code. The frozen triage baseline and its hashes are also fixtures; preserve their exact bytes. Date-named agent preparation scripts describe the original protocols and create new output when explicitly invoked. Recipe preparation depends on health preparation; report collectors require newly generated worker data.
