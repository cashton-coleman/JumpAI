# Frequently asked questions

For installation and a quick introduction, start with the [README](README.md). For implementation details, see the [developer guide](DEVELOPER_GUIDE.md).

## What comes ready to use?

The default installation includes tools for inspecting Python source, running selected unittest checks, gathering failure evidence, making guarded text replacements, and assembling reports. A bundled repair workflow connects those steps and pauses when the agent needs to choose a fix.

Additional examples show how to load data, calculate summaries, and build custom workflows. The benchmark's source-review recipe is a prepared example; it is not automatically installed as a ready-to-run recipe for every project.

## Will I have to keep reminding the agent?

You should not need to. The installed instructions assign the agent responsibility for finding existing tools, capturing substantial reusable work, verifying it, and remembering it for later. These steps should happen as part of doing your task, without a separate request to save things. Authorized delegated agents must inherit the applicable guidance or receive it in their assignment.

The automation depends on the agent following those instructions. `doctor` checks the installation, not whether an agent consistently performs every step. The [prepared-recipe comparison](HOW_IT_WORKS.md#what-we-measured) measured reuse, not the complete automatic capture-and-reuse cycle.

## Does Jump AI learn automatically?

The skill instructs the agent to create and verify useful procedures as it works, and Jump AI stores them for reuse. The agent is also instructed to retain useful recipe commands in task context or workspace instructions. This extends its toolbox without changing the model itself.

Saving and verification are operations the agent performs; the runtime does not independently capture arbitrary commands. Trivial one-off commands do not all need to become catalog entries. The aim is to save work that is substantial and likely to be useful again.

## Are saved tools available in future chats and other projects?

With the default installed-library setup, recipes and newly created local capabilities persist in the project's writable runtime across chats using that workspace. Shared bundled tools come from the installed library. Making a locally created capability available to other projects requires a separate catalog publication step; it does not happen automatically.

A saved recipe remembers how to run the work, not its previous answer. Each new invocation uses current inputs. See [how it works](HOW_IT_WORKS.md#how-it-works).

## Do I need another account, subscription, or Python packages?

Jump AI itself requires no separate model API account or third-party Python packages. Your coding agent still uses its normal account and permissions.

This private repository requires an authorized GitHub account. The documented install command uses GitHub CLI (`gh`), Git, and Python 3.11 or newer with SQLite FTS5 support. The installer checks prerequisites but does not install Python or a compiler. Use `JUMPAI_PYTHON=/absolute/path/to/python3` with the installer if you need to select a particular interpreter.

## Do I need a C++ compiler?

No. A C++17 compiler is optional. Jump AI checks for a usable toolchain during installation and selects the runtime preference accordingly. Native programs can declare Python fallbacks; existing Python programs remain Python.

Compiled binaries are cached, and new builds run in the background. Native-only programs cannot run through a Python fallback they do not provide. See [C++ execution](DEVELOPER_GUIDE.md#c-execution-and-binary-caching) for the details.

## Will every task be faster or cheaper?

No fixed saving is guaranteed. Jump AI is most useful when it avoids repeatedly generating utility code, coordinating predictable steps, and passing intermediate results through the model. A simple shell command may already be the best approach for a small task.

The measured comparison used one agent per approach and a prepared, warmed recipe. Its total token counts include cached input, so the percentage reduction is not a billing estimate. Read [the results and their limits](HOW_IT_WORKS.md#what-we-measured).

## Does it work with coding tools other than Codex?

Codex is the primary installation target. Other coding tools can use the runtime with their own instruction-loading setup; see [other-host installation](skills/jumpai/references/installation.md#other-agent-hosts).

Python and C++ are supported today. GPU kernels and an MCP server are not implemented. Install separately on remote machines or containers; local paths and toolchains do not transfer automatically.

## What permissions does it use, and what does it save?

Jump AI runs locally with the agent's existing permissions. It does not bypass host restrictions or make unfamiliar code safe to execute.

Saved recipes retain arguments, and paused workflows retain inputs, decisions, and evidence locally. Keep secrets out of them. The [developer guide](DEVELOPER_GUIDE.md#permissions-privacy-and-reliability) explains storage and execution boundaries.

## What working standards are included?

The bundled standards call for readable code, useful comments, focused tests, concise communication, and reuse of matching Jump AI capabilities. They apply to Jump AI-assisted work and authorized delegated agents. [Read the standards](skills/jumpai/AGENTS.override.md).

## What if I already downloaded Jump AI or want a different install location?

The GitHub bootstrap refuses to overwrite an existing destination. From a complete existing checkout or an extracted release, run:

```sh
./install.sh
```

Keep the installation directory in place because the skill and runtime refer to it. For a custom location or interpreter, see the [installation guide](skills/jumpai/references/installation.md). You can review the [bootstrap script](install-from-github.sh) before running it.

## How do I check or troubleshoot the installation?

From the default installation directory:

```sh
cd "$HOME/JumpAI"
./install.sh doctor
```

Start a new Codex chat after installation or updates; restart the app if skill discovery has not refreshed. `doctor` checks the skill link, runtime, startup guidance, and bundled capability availability. More troubleshooting is in the [developer guide](DEVELOPER_GUIDE.md#updates-removal-and-troubleshooting).

## How do I update Jump AI?

For a Git installation:

```sh
cd "$HOME/JumpAI"
git pull --ff-only
./install.sh
```

Start a new chat afterward. For an archive installation, consult the [installation guide](skills/jumpai/references/installation.md) before moving the checkout or replacing its skill link.

## How do I uninstall it?

```sh
cd "$HOME/JumpAI"
./install.sh uninstall
```

Uninstall removes the managed skill link and instructions while preserving your catalog and unrelated settings. Locally modified managed content is reported rather than silently deleted. Substitute your installation directory if you chose a different location.
