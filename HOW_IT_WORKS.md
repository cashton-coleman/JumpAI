# Jump AI: results and how it works

How reusable programs reduce repeated agent work, what we measured, and how Jump AI runs them. For installation and everyday use, return to the [README](README.md).

[What we measured](#what-we-measured) · [Why it saved work](#why-it-saved-work) · [How it works](#how-it-works)

## What we measured

We gave two separate agents the same task using real Jump AI repository files:

- Inventory 19 source files and count their bytes and lines.
- Check for TODO/FIXME markers and trailing whitespace.
- Summarize 27 previously recorded test outcomes.
- Combine everything into one report.

One agent wrote the computation code itself using ordinary tools. The other ran a saved Jump AI recipe chaining four cached C++ programs. Both used the same model, and their reports matched after normalizing two field names.

| Whole-task measurement | Agent writing the code | Agent using Jump AI | Difference |
| --- | ---: | ---: | ---: |
| Elapsed time | 22.6 seconds | 10.7 seconds | **52.6% less time** |
| Total tokens, including cached input | 107,305 | 69,140 | **35.6% fewer** |
| Generated output tokens | 874 | 234 | **73.2% fewer** |
| Model exchanges | 3 | 2 | One fewer |
| Tool calls | 2 | 1 | One fewer |

These are actual agent usage counters and whole-task timings, including the agents' final responses—not estimates based on the length of a script.

**What this result means:** a known workflow can save substantial agent work on a repeat task. This was one run per approach, with the recipe and native binaries already prepared. The Jump AI agent received the recipe command directly, so this comparison measured reuse, not automatic discovery or learning. It excludes the one-time work of building the capability, compiling it, preparing the comparison, and auditing the results. It summarized existing test results; it did not run those 27 tests again. It is evidence of useful reuse, not a promised speedup for every task.

Total tokens include cached prompt input, which can be priced differently. The reduction is **not a claim of 35.6% lower cost**. See the [measurement details and saved evidence](DEVELOPER_GUIDE.md#measured-workflow-comparison) for the breakdown.

### Why it saved work

The ordinary agent had to produce the code that carried out the task. The Jump AI agent already had a working recipe and could execute it directly.

That avoided repeated code generation and a model/tool exchange. The four programs passed their results to each other inside Jump AI, so the agent did not have to coordinate every step or carry intermediate data through its conversation.

All four workflow steps used cached C++ binaries. **The broader opportunity is reducing the work the model has to do:** less code generation, fewer exchanges, and less repeated context. This comparison changed both reuse and execution approach; it does not establish a separate C++ speed benefit. A trivial task may be just as quick with an ordinary shell command. A workflow that still causes repeated searches, reads, and checks can lose its advantage.

## How it works

Think of Jump AI as a toolbox your agent can keep improving:

1. **Save a tool.** A useful program gets a name, version, purpose, and clear inputs and outputs.
2. **Connect the steps.** A workflow combines several tools into a repeatable task.
3. **Remember the invocation.** A recipe saves a program or workflow and its arguments so the next run can skip discovery.
4. **Ask only when judgment is needed.** A workflow can pause with relevant evidence, accept the agent's decision, and continue without repeating completed steps.

Jump AI uses a local SQLite database to remember tools and recipes. Each new invocation runs against current inputs. A paused workflow keeps its completed steps so it can continue from the decision point. **A saved recipe reuses the method, not yesterday's answer.**

The default installation provides a shared library of bundled tools, with saved recipes and new local capabilities kept in the project's writable runtime. They persist across chats using that workspace. Publishing a locally created capability for other projects is a separate catalog operation; installation does not automatically share everything an agent creates.

For C++ programs, Jump AI also caches compiled binaries. On use, a new registered source version or detected changes to the toolchain or dependencies can require a background build. Programs with a declared Python fallback can run while that work is pending; native-only programs return a pending response. Existing Python programs stay Python, and source changes must be registered as a new version.

The [developer guide](DEVELOPER_GUIDE.md) covers the architecture, commands, checkpoints, and how to add your own tools.
