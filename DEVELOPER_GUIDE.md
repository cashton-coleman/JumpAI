# Jump AI developer guide

This guide covers the runtime, command-line interface, capability authoring, and measurement details. For installation and everyday use, start with the [README](README.md).

## Using the commands in this guide

Install Jump AI first. For manual use of the default installed library, define this optional function in your current shell. Use the interpreter selected during installation if `python3` differs:

```sh
# Forward commands to the installed library, keeping mutable state in the workspace.
jumpai() {
  python3 "$HOME/.agents/skills/jumpai/scripts/jumpai.py" \
    --library "$HOME/.local/share/jumpai/library.json" "$@"
}
```

Run from your project or supply `--workspace /absolute/project` before the subcommand. Agents should use the installation-specific command already supplied in their startup guidance. Catalog administration examples use `python3 -m jumpai` from the checkout with an explicitly writable `--home` instead.

Jump to [workflows](#conditional-workflows-and-checkpoints), [C++](#c-execution-and-binary-caching), [authoring](#build-your-own-capabilities), [commands](#command-reference), or [benchmark evidence](#measured-workflow-comparison).

## Programs, workflows, and recipes

| Term | Meaning | Example |
| --- | --- | --- |
| **Program** | A versioned executable with a JSON input/output contract | Load JSON records or collect source definitions |
| **Workflow** | Ordered, pinned programs connected by input/output bindings | Load records → compute statistics |
| **Resolution** | A remembered request phrase mapped to a version | “summarize benchmark JSON” → `summarize.json@1.0.1` |
| **Recipe** | A pinned target plus explicitly saved input arguments | Reuse a particular measurements file and numeric field |
| **Session** | One persisted workflow execution with checkpoint state | Pause a failing-test repair, then continue with a decision |

A workflow defines **what steps run**. A recipe remembers **how to invoke a target**. A recipe can invoke either a program or a workflow. Starting a recipe again performs fresh work against current inputs; it does not return a previously computed answer.

## Try a complete example

This example loads bundled measurements and summarizes their `duration_ms` field. It uses an isolated demonstration catalog inside the checkout, leaving the installed library alone.

Run the commands in the same shell:

```sh
cd "$HOME/JumpAI"

# Register and verify the demonstration programs and their composed workflow.
export JUMPAI_HOME="$PWD/.jumpai/readme-demo"
python3 examples/bootstrap.py --home "$JUMPAI_HOME"

# Execute once, remember the request, and save a direct recipe.
python3 -m jumpai run --target summarize.json@1.0.1 \
  --input '{"path":"examples/measurements.json","field":"duration_ms"}' \
  --remember 'summarize benchmark JSON' \
  --save-recipe measurements

# Replay the saved invocation without discovery or rewritten utility code.
python3 -m jumpai jump measurements

# Alternatively, reuse the request mapping with explicit arguments.
python3 -m jumpai run --query 'summarize benchmark JSON' \
  --input '{"path":"examples/measurements.json","field":"duration_ms"}'

python3 -m jumpai stats
unset JUMPAI_HOME
```

The execution receipt's `output` is:

```json
{"count": 3, "min": 10, "max": 30, "mean": 20, "median": 20}
```

The replay and remembered-query run report `cache_hit: true`. The load and summary programs execute inside the runner; their intermediate records need not pass through the agent.

A successful `--save-recipe` returns a **recipe card** containing a shell-quoted replay command with absolute catalog and workspace paths. Preserve that command, purpose, and applicability in the task context or workspace instructions to help a future agent skip setup reads.

`jump measurements --with '{"path":"other-measurements.json"}'` replaces top-level arguments for that invocation only. It does not alter the saved recipe. Use the same catalog, workspace, and context as the original invocation; the returned command card captures those details.

## Conditional workflows and checkpoints

A workflow can run deterministic steps, skip branches with `when`/`otherwise`, and pause where the model needs to decide. The shipped regression workflow follows this path:

```text
Run selected tests
  ├─ Passing → return report
  └─ Failing → collect evidence → pause for repair decision
                                  ↓
                     verify source digest → apply replacement
                                  ↓
                        rerun selected tests → report
```

Using the installed-library shell function from above, substitute your real project, source file, and unittest identifier:

```sh
jumpai --workspace /absolute/project run \
  --target repo.regression-triage@1.4.0 --resumable \
  --input '{"root":"/absolute/project","source_path":"package/normalize.py","tests":["test_normalize.NormalizeTests.test_whitespace"]}'
```

A passing run completes without a repair. A failing run returns `status: "needs_decision"`, a session ID, the pending step, a decision schema, and evidence. The evidence collector includes relevant complete Python definitions, failure facts, a source digest, explicit omissions, and a reference to fuller local evidence. Enclosing class/module context may be omitted; read additional context only when it matters to the decision.

After choosing an authorized repair, resume with that session ID and the exact pending step. This is illustrative replacement data; choose the actual text from the evidence:

```sh
jumpai --workspace /absolute/project resume SESSION_ID --step repair \
  --decision '{"path":"package/normalize.py","old":"return value","new":"return value.strip()"}'
```

The replacement runs only if the source digest still matches and the old text occurs exactly once. The workflow then performs final checks and reporting. Completed steps are retained rather than replayed.

Use `status SESSION_ID` to inspect progress, or `result SESSION_ID --step STEP_NAME` to retrieve details, under the same workspace and catalog. Do not fetch full results when the compact receipt already answers the question.

Only explicit decision checkpoints can resume. Failed or interrupted executions require diagnosis; sessions do not provide automatic crash recovery, rollback, or exactly-once external side effects. Concurrent/duplicate resumes and stale step names are rejected.

To save a recipe from a paused run, pass `--save-recipe NAME` on the **completing resume**. A recipe stores original arguments, not the decisions or previous output. See the [workflow authoring reference](skills/jumpai/references/authoring.md#conditional-and-resumable-workflows) for schemas and size limits.

## Delegated work

Delegation helps only if the child avoids repeating the parent's inspection and checks. For authorized delegated unittest diagnosis:

```sh
jumpai --workspace /absolute/project handoff --prepare --read-only \
  --source package/normalize.py \
  --tests '["test_normalize.NormalizeTests.test_whitespace"]'
```

Preparation runs the checks once. If a decision is needed, it returns `child_assignment` and `resume_command`:

1. Pass `child_assignment` unchanged to one fresh diagnosis child.
2. The child uses the enclosed evidence and returns a decision and brief reason, without tools, repeated checks, or further delegation.
3. The parent waits, reviews the decision, and supplies it to the returned resume command only when source edits are authorized.
4. The workflow applies the guarded edit and performs final checks once.

A healthy preparation needs no child. If a specific missing fact prevents a safe decision, the child returns `needs_evidence`; the parent retrieves only that context. `--read-only` permits preparation, not automatic authorization for a later repair. Jump AI prepares the handoff; the host performs the actual agent delegation.

## C++ execution and binary caching

Jump AI supports Python programs and single-translation-unit C++17 programs with bundled headers. A native bundle may declare an equivalent Python fallback.

Installation compiles, links, and runs a small C++ probe. A usable toolchain selects the C++ preference; otherwise Python is selected. **This does not translate Python programs into C++.** Only bundles authored with `runtime: "cpp"` use native execution.

```sh
jumpai settings
```

Native binaries live in the workspace runtime cache. Their identity accounts for bundled source, compiler/toolchain identity, build policy, host, and relevant compiler-selection environment. Source and toolchain changes invalidate reuse.

| Situation | Behavior |
| --- | --- |
| Valid warm binary | Reuse it after integrity/dependency validation |
| Missing or changed binary | Build in the background; run the declared Python fallback while pending |
| Native-only bundle while pending | Return a pending error promptly so the agent can do other work |
| Failed build | Retain the error; avoid recompiling on every call; use the declared fallback when applicable |
| Native execution fails | Report the failure; do not silently rerun through Python |
| Explicit `verify` | Wait for the native build so fixtures actually exercise C++ |

Compiler identification is cached. On macOS/BSD, file-event watches avoid repeatedly scanning compiler headers on warm hits. Unsupported hosts or failed subscriptions use conservative dependency checks. Slow or unavailable watchers never authorize unvalidated reuse.

Inspect `native_build` and workflow `native_steps` evidence when measuring the backend. A cold C++ request may execute its Python fallback; a fast recipe lookup alone does not prove that a native binary ran. Avoid repeated polling while builds are pending.

To change the shared runtime preference, use the writable catalog and publish the updated settings. From the checkout with the default catalog:

```sh
python3 -m jumpai --home "$HOME/.local/share/jumpai" settings --prefer python
python3 -m jumpai --home "$HOME/.local/share/jumpai" publish-library
```

Use `--prefer cpp` after successful toolchain detection to switch back. Reinstallation probes again and resets the preference from detected capability. [Native authoring and cache details](skills/jumpai/references/authoring.md) explain fallback semantics, worker behavior, and build recovery.

## How the caches work

Jump AI uses several distinct kinds of reuse:

| Stored item | Saves | Does not save |
| --- | --- | --- |
| Program bundle | Rewriting an implementation | Running the program |
| Resolution | Repeated discovery and selection | Supplying new arguments |
| Recipe | Repeated discovery and argument setup | Fresh execution |
| Native binary | Repeated compilation | Runtime input/output validation |
| Workflow session | Repeating completed steps in the same paused run | Arbitrary recovery after side effects |

Resolution keys combine a case-folded, whitespace-normalized phrase, resolved workspace path, and exact context string. There is no fuzzy cache matching: a paraphrase needs another selection or an explicit alias through `remember`.

Bindings pin immutable versions and a catalog revision. New registrations, verification changes, and enable/disable changes invalidate older bindings conservatively. Identical re-registration is a no-op. A missing or stale recipe returns exit code **3** without executing; select a valid target and save a new successful invocation.

External files, APIs, or environment changes do not automatically change the catalog revision. Use `--context` for applicability differences and reverify when dependencies change. Jump AI checks program integrity and executes against current inputs; it does not cache final answers.

## Storage and architecture

```text
Agent / installed skill
         │ known recipe, pinned target, or discovery
         ▼
CLI → workspace SQLite catalog → validated program or workflow
         ▲                              │
Read-only installed library             ├─ Python subprocess
                                        ├─ cached C++ executable
                                        └─ decision checkpoint → agent
```

Default installation locations:

| Location | Contents |
| --- | --- |
| `~/JumpAI` | Complete source checkout used by the skill and runtime |
| `~/.agents/skills/jumpai` | Skill link to that checkout |
| Effective global Codex instruction file | Managed Jump AI guidance and working standards |
| `~/.local/share/jumpai` | Shared catalog, registered artifacts, host execution settings |
| `~/.local/share/jumpai/library.json` | Published definitions and availability for workspace imports |
| `<project>/.jumpai/runtime/<library-identity>` | Writable SQLite state, recipe/resolution caches, sessions, run history, native binaries |

Published libraries let sandboxed agents read shared definitions while writing only inside the actual workspace. They do not change host permissions. Reinstall to refresh guidance and bundled capabilities; `publish-library` publishes catalog changes alone. Workspace-local capabilities are not automatically promoted into the shared library.

SQLite stores program/version metadata, ordered workflow steps, FTS5 discovery, resolutions, recipes, sessions, settings, and run records. Source bundles live separately under `artifacts/<sha256>/`. Registration uses content-addressed artifacts and transactional metadata; SQLite uses WAL, foreign keys, and a busy timeout.

Run history stores execution metadata rather than inputs, outputs, or stderr. Recipes explicitly persist arguments. Resumable sessions separately persist inputs, decisions, and intermediate/final results. Back up the entire catalog home and relevant workspace state; artifact paths are absolute in this release, so catalogs are not freely relocatable.

## Build your own capabilities

Start with the [authoring guide](skills/jumpai/references/authoring.md) and [example bundles](examples/programs). A program bundle contains:

```text
my-program/
  manifest.json    # Purpose, version, runtime, schemas, entrypoint, explicit file list
  main.py          # Or a C++ entrypoint and optional Python fallback
  cases.json       # Representative input/output fixtures
```

Programs read one JSON value from stdin and write exactly one JSON value to stdout. Diagnostics go to stderr. Invocation-specific arguments travel as JSON; manifest `arguments` are fixed command arguments. Programs execute in the selected workspace with the caller's environment.

From the checkout, use an explicitly writable development catalog:

```sh
python3 -m jumpai --home /absolute/dev-catalog register /absolute/my-program/manifest.json
python3 -m jumpai --home /absolute/dev-catalog verify my.program@1.0.0 \
  --cases /absolute/my-program/cases.json --cwd /absolute/project
python3 -m jumpai --home /absolute/dev-catalog run --target my.program@1.0.0 \
  --input-file /absolute/request.json --cwd /absolute/project
```

Replace the illustrative target and paths with your manifest and fixtures. Register only required files. New versions start experimental; verify representative cases before remembering or saving recipes. Source or contract changes require a new version. Registration of identical content is idempotent.

Contracts use an enforced subset of JSON Schema; unsupported constraints are rejected. Workflows bind inputs to pinned programs, previous step outputs, or literals. Verify components first, then their composition. The [summarize workflow](examples/workflows/summarize-json.json) is a small complete example; the [regression workflow](workflows/regression-triage-complete.json) demonstrates conditions and decision checkpoints.

## Command reference

Commands below follow a launcher such as the installed-library `jumpai` function above. Administration of shared definitions/settings requires an explicit writable `--home` launcher instead of that function.

| Command | Purpose |
| --- | --- |
| `init` | Create/open a catalog |
| `search 'phrase' --limit 5` | Find short full-text candidates |
| `inspect name@version` | Read a complete contract |
| `resolve 'phrase'` | Check a resolution binding; return candidates on a miss |
| `run --target name@version --input '{...}'` | Execute a pinned version |
| `run --query 'phrase' --input '{...}'` | Execute only an existing remembered resolution |
| `run ... --remember 'phrase'` | Remember after successful validated execution |
| `run ... --save-recipe name` | Save successful arguments and return a replay card |
| `jump name --with '{...}'` | Replay a recipe with optional top-level replacements |
| `run ... --resumable` / `jump name --resumable` | Execute with persistent checkpoints and compact receipts |
| `resume SESSION --step NAME --decision '{...}'` | Validate a decision and continue |
| `status SESSION` | Read progress without executing |
| `result SESSION [--step NAME]` | Retrieve completed output or one stored step |
| `handoff --prepare --read-only --source FILE --tests '[...]'` | Prepare a diagnosis assignment and resume command |
| `register manifest.json` | Snapshot an immutable program/workflow version |
| `verify name@version --cases cases.json` | Run fixtures and set validation status |
| `remember 'phrase' name@version` | Bind an already validated capability without executing it |
| `enable name@version` / `disable name@version` | Change availability and invalidate bindings |
| `settings` | Inspect native/Python execution preference |
| `publish-library` | Publish shared definitions for workspace imports |
| `stats` | Inspect catalog counts, reuse hits, and execution counts |

Global `--home`, `--library`, and `--workspace` options precede the subcommand. `--home` and `--library` are mutually exclusive. `run` supports `--input-file PATH` or `--input-file -` for stdin. Execution commands accept `--cwd`; `resolve`/`remember` accept `--scope`. Resolution/recipe operations support an exact `--context` key.

Successful operations print JSON. Operational errors print JSON to stderr and return **2**; invalid CLI syntax uses argparse usage output. A remembered-query miss or missing/stale recipe returns **3** without execution. Interruption returns **130**. A successfully returned check report can still contain failed tests, and a checkpoint can still need a decision: inspect result fields as well as the process exit code.

## Working standards and persistence

The bundled [AGENTS.override.md](skills/jumpai/AGENTS.override.md) is the canonical source of Jump AI's standards. Installation embeds its contents in managed host startup guidance so future chats receive:

- Required use of matching available Jump AI mechanics before equivalent ad hoc code.
- Concise, readable code with descriptive names, spacing, and useful function documentation.
- Input validation, resource cleanup, data integrity, and explicit failure handling.
- Focused tests with the same readability and hygiene standards as production code.
- No unnecessary engineering, documentation, testing, or reporting.
- Applicable instructions and prepared handoffs for authorized delegated agents.

Trivial work, unavailable capabilities, host restrictions, and explicit user exceptions remain valid reasons to use ordinary tools. Instruction-based persistence depends on the host loading guidance; it is not a hard enforcement layer. `doctor` checks installation structure, not whether a particular model obeyed every instruction.

## Permissions, privacy, and reliability

Jump AI is a **trusted local code library**. Programs run with the caller's permissions and inherited environment. Registration and passing fixtures do not grant authority, establish a sandbox, or prove code safe. Inspect unfamiliar programs and retain the host's normal execution controls.

The core does not need a cloud service or model API account. GitHub installation needs network access; individual programs can use network access if their code and host permissions permit it. Recipes and resumable sessions retain data locally, so avoid putting secrets in saved arguments or decision evidence. Prefer file references for large or sensitive material.

The runner validates inputs/outputs, checks registered source integrity, applies timeouts, and bounds captured output. The stdout/stderr limit is 2 MiB, monitored at intervals; it is not a hard OS resource quota. On POSIX, timeout/interruption cleanup kills the process group; Windows cleanup currently targets the direct process.

Workflows stop on failure. Earlier side effects can remain; there is no automatic rollback or blind retry. Guarded editing reduces stale-evidence risk but does not make every possible program transaction-safe. Reliability is an engineering goal; no defense certification or universal correctness claim is made.

## Updates, removal, and troubleshooting

From the installed checkout:

```sh
cd "$HOME/JumpAI"
git pull --ff-only
./install.sh
./install.sh doctor
```

Reinstallation refreshes the managed instructions and bundled definitions. Start a new chat afterward. For an archive installation, see the [installation guide](skills/jumpai/references/installation.md) before moving the checkout or replacing a skill link.

To remove the managed installation:

```sh
cd "$HOME/JumpAI"
./install.sh uninstall
```

Uninstall preserves the catalog and unrelated instructions. Locally modified managed content or an unrelated replacement skill is reported rather than silently deleted.

| Symptom | Next step |
| --- | --- |
| Bootstrap says the destination exists | Use the checkout's `./install.sh`, or choose a new `JUMPAI_INSTALL_DIR` |
| Download returns 404 | Confirm the bootstrap has been published to the requested branch and the repository is accessible |
| Python/FTS5 prerequisite fails | Select a compatible interpreter with `JUMPAI_PYTHON`, then rerun installation |
| Skill is missing in a fresh chat | Run `./install.sh doctor`; check discovery, disabled-skill settings, and startup instruction limits |
| Agent tries to write the shared catalog | Use the supplied `--library` command from the actual task workspace |
| Recipe is missing or stale | Check workspace/context; select a valid target and save a successful invocation again |
| Source digest or exact replacement fails | Inspect the changed source and prepare fresh evidence before another repair |
| Native build is pending | Use the declared fallback or continue independent work; inspect build evidence when needed |
| Native build failed | Read the recorded build error and repair the toolchain/source; do not poll or blindly delete active builds |
| Uninstall reports edited managed content | Reconcile that specific block/link instead of removing unrelated user settings |

## Compatibility and current limits

Codex is the primary installation target. Other coding tools can use the same CLI, manifests, and runtime through the [generic installation adapter](skills/jumpai/references/installation.md#other-agent-hosts), provided they load the supplied skill and startup instructions. Paths and instruction-loading rules depend on the host. The repository includes [Claude instruction imports](CLAUDE.md), but a live Claude Code installation has not been validated here.

Current support includes Python, C++17, sequential workflows, conditional steps, explicit checkpoints, and workspace-local reuse. It does **not** include GPU kernels, automatic dependency provisioning, nested or parallel workflows, final-result caching, or an MCP server. Native Windows shell installation is not covered by the commands above; runtime code includes some Windows support, including MSVC detection, with the process-cleanup limitation described earlier.

Install separately on remote machines and containers. Local compiler paths, artifacts, recipes, and skill links do not automatically transfer to another machine.

## Measured workflow comparison

The [published comparison](HOW_IT_WORKS.md#what-we-measured) comes from one completed pair of fresh agents using the same model (`gpt-6-astra`). The retained [review receipt](benchmarks/native-workflow-result.json) records actual agent telemetry, correctness checks, native build evidence, and workflow run timings. It is a historical measurement, not a rerun against every subsequent version of Jump AI.

The task inventoried 19 frozen repository source files (198,517 bytes), scanned literal TODO/FIXME markers and trailing whitespace, summarized 27 already-recorded test outcomes, and assembled a report. It did not execute those tests. The ordinary agent wrote its own computation code and could choose its language, batch work, and chain commands. The recipe agent received a complete invocation for `repo.native-review@1.0.0`, with all four C++ steps already warmed.

| Actual whole-agent counter | Ordinary code | Prepared recipe |
| --- | ---: | ---: |
| Wall time | 22.600 s | 10.706 s |
| Model exchanges | 3 | 2 |
| Tool calls | 2 | 1 |
| Input tokens | 106,431 | 68,906 |
| Cached input tokens (included above) | 102,912 | 65,408 |
| Output tokens | 874 | 234 |
| Total tokens | 107,305 | 69,140 |
| Uncached input plus output, derived | 4,393 | 3,732 |

The reductions are 52.6% in wall time, 35.6% in total tokens, and 73.2% in output tokens. Excluding cached input, the derived input-plus-output reduction is **15.0%**. These counters do not establish a billing reduction: pricing depends on the model, token category, and host/account arrangement. One fewer exchange reduced repeated prompt processing, much of which was cached.

The reviewer confirmed unchanged inputs and equivalent reports, normalizing the ordinary agent's `total_bytes`/`total_lines` keys to `bytes`/`lines` because the prompt had not fixed those two names. All four native receipts were ready and matched warmup evidence. Run history contained exactly one successful workflow and four successful child runs. Its inner workflow time was **75.154 ms**; that is distinct from the **10.706-second whole-agent time** and must not be presented as the complete task latency.

This measured reuse after preparation. One-time authoring, verification, compilation, setup, and the parent's audit are outside the agent totals; no end-to-end setup break-even is established. The recipe agent received its invocation directly, so this comparison does not measure discovery or automatic adoption. It also does not isolate C++ speed from saved code generation, reduced context, or fewer exchanges. One agent per condition cannot establish variance or general performance across workloads.

The receipt was retained from the original local `review.json`; full agent traces and temporary workspaces are not distributed. The review logic is available in [review_native_workflow.py](benchmarks/review_native_workflow.py), the task preparation in [prepare_native_workflow.py](benchmarks/prepare_native_workflow.py), and the chained definition in [native-source-review.json](workflows/native-source-review.json). This is separate from the earlier command-payload microbenchmark described in [methodology.md](benchmarks/methodology.md).

## Performance and validation

The largest opportunity is avoiding repeated model work: generating the same utility code, loading the same contracts, relaying intermediate data, and repeating checks across agents. A saved recipe and a well-designed workflow can collapse those exchanges into one invocation plus any necessary decision.

C++ can reduce program execution time, but whole-task time also includes model generation, tool dispatch, Python runner startup, contract/integrity checks, filesystem work, and coordination. Very short tasks may see little benefit. Discovery overhead or repeated evidence reads can erase savings.

When comparing Jump AI with ordinary agent work:

- Give separate agents the same task, inputs, and acceptance criteria.
- Count the whole task, including setup, tool calls, model responses, and final output.
- Record actual total tokens and model exchanges; distinguish cached input from uncached input and output.
- Separate cold setup/compilation from warm execution and verify the actual backend.
- Compare correctness and side effects before claiming a speedup.

Run records measure program execution, not total model token usage. Token totals must come from the host's actual usage records. Total tokens including cached input are not monetary cost estimates. See [benchmark methodology](benchmarks/methodology.md) for measurement boundaries. No universal savings percentage is claimed.

### Maintainer release check

For a focused clean-package check and release build:

```sh
python3 -m unittest discover -s tests -p test_release.py -v
python3 scripts/package_release.py
```

The release check builds/extracts a complete archive, routes the bootstrap clone to a local Git fixture, installs in isolation, executes and replays a workflow, and verifies uninstall preserves user data. It does not test an unpublished GitHub URL over the network.

Packaging writes `dist/jumpai-VERSION.tar.gz` and a SHA-256 file. Upload both to the matching release after verification. Local catalogs, compiled binaries, credentials, and generated benchmark reports are excluded. Packaging does not publish anything.

For development, run the smallest relevant checks; expand testing only for a concrete failure or risk. The broader suite covers catalog integrity, schema enforcement, cache reuse/invalidation, workflows, native execution, and installer behavior. Generated benchmark reports are disposable ignored outputs; keep reusable fixtures and methodology in the repository.

## Contributing and license

Follow [AGENTS.md](AGENTS.md) and the shared [working standards](skills/jumpai/AGENTS.override.md). Keep contributions focused, document contracts and failure behavior, and add representative fixtures for reusable capabilities. Native programs and Python fallbacks must preserve the same required semantics, including Unicode, numeric precision, ordering, and errors.

Useful entry points:

| Path | Contents |
| --- | --- |
| [`jumpai/`](jumpai) | Catalog, CLI, runtime, workflows, installation, and native cache |
| [`skills/jumpai/`](skills/jumpai) | Installable skill, launchers, standards, and detailed references |
| [`programs/`](programs) | Reusable program bundles |
| [`workflows/`](workflows) | Workflow definitions |
| [`examples/`](examples) | Small complete examples and bootstrap |
| [`tests/`](tests) | Behavioral and regression checks |
| [`scripts/package_release.py`](scripts/package_release.py) | Complete source-release packaging |
| [`benchmarks/methodology.md`](benchmarks/methodology.md) | Measurement approach and limitations |

Jump AI is distributed under the [MIT license](LICENSE). Bundled third-party code retains its [original licenses and attribution](THIRD_PARTY_NOTICES.md).
