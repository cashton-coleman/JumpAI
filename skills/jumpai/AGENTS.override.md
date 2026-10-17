# Jump AI working standards

These requirements apply to code written or changed while using an installed Jump AI skill, including runtime code, native programs, Python fallbacks, scripts, examples, benchmarks, and tests. Apply them without expanding the task into an unrelated cleanup. Preserve third-party licenses and upstream formatting.

## Mandatory use when installed

- When Jump AI is installed and enabled, you must execute a matching available capability before writing equivalent ad hoc code for reusable inspection, guarded edits, preparation, checks, or chained workflows. Reading the skill without using the matching capability does not satisfy this requirement.
- Use installation guidance, the available-skill listing, or a known working command as evidence of installation. Do not repeatedly probe availability. Execute known recipes and pinned contracts directly; otherwise load the skill and resolve once.
- Ordinary tools remain appropriate for trivial commands, work with no suitable capability, an unavailable or blocked installation, or an explicit user exception such as a benchmark control. Report a material blocker briefly and continue useful authorized work; do not bypass host permissions or install tools merely to satisfy this rule.
- These requirements apply to parent and delegated agents, including empty-history agents. When delegation is authorized and the child will not inherit startup guidance, include these standards and the applicable Jump AI command or prepared handoff in its assignment. Do not add extra delegation or repeated reads just to confirm compliance.

## Code hygiene and human readability

- Write concise, straightforward code with descriptive names and small, focused functions. Prefer explicit control flow over clever expressions, dense one-liners, or unnecessary abstractions.
- Separate functions and logical sections with blank lines. Give each module/file a purpose header and each meaningful section a short, descriptive comment header.
- Document every function and method with a concise comment or docstring covering its purpose and relevant inputs, outputs, side effects, and failure behavior. Explain non-obvious decisions and constraints; do not narrate obvious statements or repeat signatures.
- Keep comments accurate when behavior changes. Remove obsolete code and misleading comments within the changed area.
- Use the smallest sound design that meets the request. Do not add speculative features, frameworks, layers, dependencies, or configuration options.

## Reliability

- Treat defense-grade reliability as an engineering goal: validate external inputs and trust boundaries, enforce contracts, and handle meaningful edge cases explicitly.
- Preserve data integrity, release resources reliably, bound subprocess work, and handle concurrency and partial failure where the task requires them. Prefer atomic publication for persistent state and RAII for C++ resources.
- Fail safely with actionable errors. Do not silently swallow failures, substitute fabricated results, or retry potentially side-effecting work without establishing that retry is safe.
- Preserve required semantics across native implementations and fallbacks, including Unicode, numeric precision, ordering, and error behavior.
- Never claim code is bulletproof, DoD-certified, or compliant with a defense standard without a defined standard and supporting evidence.

## Tests use the same standards

- Apply the same naming, spacing, function documentation, section comments, simplicity, and resource cleanup rules to tests, fixtures, and test helpers as to production code.
- Cover changed behavior with focused success, boundary, and meaningful failure cases. Add a regression test for a fixed defect when practical; verify observable behavior rather than mirroring implementation details.
- Use deterministic inputs, isolated temporary state, and clear assertions. Avoid unnecessary mocks, broad snapshots, timing-sensitive sleeps, and external-service dependencies.
- Run the smallest relevant checks that establish correctness. Expand only for a concrete failure, unresolved risk, required repository check, or explicit request. No exhaustive suites or coverage-percentage targets by default.
- For native/fallback changes, check equivalent outputs and relevant errors. For performance claims, verify the actual backend and separate cold setup from warm execution.
- Documentation-only changes need a focused content review, not a new test suite. Report checks accurately, including failures and untested limitations.

## Work efficiently and communicate briefly

- Use matching Jump AI capabilities before recreating reusable coding mechanics. Execute known recipes directly; do not repeat skill/contract reads, searches, checks, or evidence gathering already satisfied by current results.
- Keep changes focused. No over-engineering, over-documentation, unnecessary reports, or benchmark artifact proliferation. Prefer updating an existing relevant document.
- Keep chat updates concise and useful: state the result, essential evidence, next action, or material blocker. Avoid running commentary, repeated summaries, and implementation detail the user did not request.
- Final responses should briefly state what changed, what was verified, and any material limitation. Provide longer explanations only when requested or needed for a decision.
- Before finishing, review changed code and tests against these standards and correct violations within scope. Instructions guide agent behavior; they do not replace executable checks or justify unsupported quality claims.
