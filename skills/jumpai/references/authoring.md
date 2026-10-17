# Authoring reusable programs

A bundle contains a `manifest.json`, its explicitly listed source files, and a verification cases file. Use the working examples in `examples/programs/` relative to the JumpAI repository as templates. The repository is at `../../..` relative to this reference's resolved directory.

A program reads one JSON value from stdin and writes exactly one JSON value to stdout. Send diagnostics to stderr. Python programs use the current interpreter; all programs use a shell-free argument array and the selected workspace as cwd. Include every local source dependency in `files`. Version 0.1 has no dependency installer; bundled examples use the standard library. Paths in arguments should refer to the execution workspace or be absolute.

For native programs, set `runtime` to `cpp` and `entrypoint` to a C++ source file in `files`. Optionally set `python_fallback` to an equivalent Python entrypoint, also in `files`, for hosts that prefer Python or lack a usable compiler. Both implementations must satisfy the same JSON contract and fixtures. Installation selects C++ only after a compile/link/run probe; `settings` exposes the selection. Native support builds a single C++17 translation unit (bundled headers may be included), with a 30-second build limit.

The runner caches binaries beneath the writable catalog's `binaries/` directory. The key includes bundled source contents, entrypoint, compiler identity/version, build policy, host, and compiler-selection environment. Compiler identification is reused while executable metadata, the Apple toolchain selector, environment, and build policy match. Metadata-only version changes reuse the same binary. New or changed builds run in detached workers at lower priority where the host permits; completed binaries are atomically published and integrity-checked before execution. Partial or outdated binaries are never used.

On macOS/BSD, a workspace watcher subscribes to compiler-reported dependency files and their ancestors before validating their identities. Every reuse requires a fresh response after draining file events, catching in-place edits, replacements, and symlink changes. Initial registration and event-triggered validation happen in the background; pending responses use Python fallback. A missing or slow watcher never authorizes reuse. Unsupported hosts or failed watch subscriptions use conservative full dependency checks. Binary integrity is still checked on each native run.

An ordinary cache miss immediately runs `python_fallback` while the build continues. Without a fallback it returns a pending error; continue other work instead of polling. `native_build` on the run receipt gives the state and build key. Failed builds retain their error and use the declared fallback without recompiling on every call; explicit `verify` and native-only programs report the failure. Native execution failures never retry through Python. Explicit `verify` waits for compilation so a Python fallback cannot accidentally validate a C++ build.

Failed or abandoned builds can be retried by removing only their `binaries/BUILD_KEY` directory after ensuring its worker has stopped. Source/toolchain changes select or rebuild the appropriate cache entry automatically. External dependency installation and cache eviction remain manual; do not delete active builds.

Required manifest fields: `name`, `version`, `purpose`, `input_schema`, `output_schema`, `runtime` (`python` or `cpp`), `entrypoint`, `files`. Optional fields: `kind: "program"`, `python_fallback` (C++ bundles only), `use_cases`, `tags`, `arguments` (fixed command-line arguments), `timeout_seconds` (default 30, maximum 3600). Invocation-specific arguments travel as JSON on stdin. Names allow lowercase letters, numbers, dots, underscores, and hyphens; use an explicit `name@version` for every execution.

Schemas use a strictly enforced subset: `type` (one of object, array, string, integer, number, boolean, null), `description`, `properties`, `required`, boolean `additionalProperties`, `items`, `enum`, `minimum`, `maximum`, `minLength`, `maxLength`, `minItems`, `maxItems`. Arrays require `items`. Unsupported keywords are rejected. Explicitly use `additionalProperties: false` for closed object contracts. No `$ref`, unions, or custom formats in this release.

Registration copies source files into a content-addressed directory and records an immutable version. A changed manifest or source requires a new version. Registration is idempotent for identical content. New versions start experimental. Run `verify name@version --cases /path/to/cases.json --cwd /workspace` using an array such as:

```json
[{"input": {"records": [{"ms": 10}, {"ms": 20}], "field": "ms"},
  "output": {"count": 2, "min": 10, "max": 20, "mean": 15, "median": 15}}]
```

Verification executes actual code with the caller's permissions and compares complete JSON output; it is not a security audit. Choose cases that cover the relevant behavior, and avoid fixtures with unintended external effects. Failure clears validated status. Use `run --allow-experimental` only for intentional development checks; such executions cannot create remembered bindings.

# Sequential workflows

Use `kind: "workflow"` with the shared descriptive and schema fields, `steps`, and `output`. Steps contain `name`, pinned `target`, and `bindings` mapping program input fields to one of:

- `{"input": "/field"}`: JSON pointer into workflow input.
- `{"step": "previous-step", "pointer": "/records"}`: JSON pointer into a preceding output.
- `{"value": 123}`: literal value.

Use an empty JSON pointer for the whole value. `output` is one binding selecting the final result. Inputs must be objects; each step validates its own inputs and outputs. Steps are sequential and stop on failure, with no automatic retry or rollback. Verify component programs first, then verify the workflow. Nested workflows and cycles are not supported in 0.1. Use `examples/workflows/summarize-json.json` as a complete template.

# Conditional and resumable workflows

Any step may include `when: {"value": <binding>, "equals": <literal>}`. Equality compares canonical JSON, distinguishing booleans from numbers. A false condition skips execution and stores `null`, or the value from an optional `otherwise` binding. Give downstream steps explicit fallbacks or matching conditions before referencing skipped results. Conditions, fallbacks, and context can refer only to inputs or earlier steps; arbitrary expression evaluation is not supported.

A decision step replaces `target` and `bindings` with:

```json
{
  "name": "choose",
  "decision": {
    "prompt": "Choose whether to continue based on this evidence.",
    "schema": {
      "type": "object",
      "properties": {"proceed": {"type": "boolean"}},
      "required": ["proceed"],
      "additionalProperties": false
    },
    "context": {"evidence": {"step": "inspect", "pointer": "/summary"}}
  }
}
```

Use `run ... --resumable` or `jump <recipe> --resumable`. Decision steps require this mode, even when a condition would skip them. A receipt with `status: "needs_decision"` provides the session ID, exact step, prompt, schema, and context. Resume with `resume <session_id> --step choose --decision '{"proceed":true}' --cwd <original-workspace>`. The validated decision becomes the step's output. An atomic claim prevents concurrent or duplicate continuations; the explicit step name rejects stale responses to later checkpoints. Reopening the catalog or restarting the CLI preserves a paused run.

Top-level optional `summary` is a binding selecting a compact completion summary (at most 2 KiB). Larger summaries are replaced by an explicit detail notice; full output remains available through `result <session_id>`. Without a summary binding, the receipt gives completion status only. `status` is read-only; `result --step <name>` retrieves one persisted step. Decision prompts are at most 500 characters, schemas 4 KiB, bound context 4 KiB, and total persisted state 8 MiB. Oversize decision context fails explicitly instead of silently omitting evidence. Put bulky evidence in files and return paths plus concise facts.

Design checkpoint evidence to support the next decision without another routine read. Include complete relevant definitions, exact test identity, concise failure facts, source digests, and explicit omissions. Never label a cut-off preview as complete. Include decorators when reporting a complete Python definition, qualify duplicate method names, and state whether enclosing class/module context is excluded. Rank whole blocks by relevance; omit lower-priority blocks rather than slicing their bodies. Keep fuller evidence in a referenced local artifact. The [complete-evidence collector](../../../programs/triage-evidence/manifest.json) and [workflow](../../../workflows/regression-triage-complete.json) demonstrate this pattern. Evidence should inform agent judgment without embedding a benchmark-specific diagnosis or repair.

Verification cases may add `decisions`, mapping checkpoint names to deterministic fixture decisions. For example, `{"input": {...}, "decisions": {"choose": {"proceed": true}}, "output": {...}}`. Include cases for taken and skipped branches. Missing or unused fixture decisions fail verification. Passing verification does not imply that every possible model decision is correct.

Resumable mode explicitly persists inputs, decisions, intermediate outputs, and the completed output in SQLite. Ordinary run telemetry still stores only outcome metadata. The runner checks pinned dependencies on resume and executes each subsequent program with its normal validation and integrity checks. External files are not automatically snapshotted: programs that modify evidence inspected before a pause should compare a saved digest before writing, as the regression-triage example does.

Only `needs_decision` can resume. Program failures mark a session failed; a hard process kill may leave it running. There is no automatic crash recovery, retry, rollback, or exactly-once guarantee for external side effects. Inspect such runs before choosing how to recover. Execution time excludes time waiting for a decision. Use the complete [regression triage workflow](../../../workflows/regression-triage.json) as an example of conditions, evidence, a model-selected patch, and rechecking.
