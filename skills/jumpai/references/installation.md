# Installation and persistence

Download the complete Jump AI checkout and run the shell installer:

```bash
curl -fsSL https://raw.githubusercontent.com/cashton-coleman/JumpAI/main/install-from-github.sh | sh
```

The bootstrap requires Git and clones into `~/JumpAI`; it refuses to overwrite an existing destination. To choose another permanent directory:

```sh
curl -fsSL https://raw.githubusercontent.com/cashton-coleman/JumpAI/main/install-from-github.sh \
  | JUMPAI_INSTALL_DIR="$HOME/tools/JumpAI" sh
```

Alternatively, extract a versioned release archive into a permanent directory and run `./install.sh`. From the installed checkout, `./install.sh plan`, `./install.sh doctor`, and `./install.sh uninstall` manage the same lifecycle. The launcher requires Python 3.11+ with SQLite FTS5; it locates a suitable interpreter without installing packages. Set `JUMPAI_PYTHON=/absolute/python` only to select a specific interpreter. It prints compact status; the underlying Python CLI remains available for integrations needing full JSON guidance.

The installer couples user-wide skill discovery, a managed startup instruction block, and a shared catalog. Merely copying `SKILL.md` does not perform this setup; skills have no universal executable installation hook. Use this installer as JumpAI's installation entry point. Keep the checkout in place because the installed skill is a symlink to it.

The skill's `AGENTS.override.md` is the canonical working-standards file; the repository override links to it. Installation embeds its full contents in the managed host startup block, so code hygiene, focused testing, concise reporting, and mandatory use of matching installed capabilities reach future chats without a separate read. Authorized child agents must inherit this guidance or receive it in their assignment. Reinstall after standards change; `doctor` detects missing or outdated embedded standards. Uninstall removes only the managed block, preserving other user guidance.

For Codex, defaults are `~/.agents/skills/jumpai`, the active global instruction file under `CODEX_HOME` (normally `~/.codex/AGENTS.md`), and `~/.local/share/jumpai`. A nonempty `AGENTS.override.md` takes precedence. `--catalog` selects a different catalog; otherwise a previous installation retains its catalog, then `JUMPAI_HOME` or the default is used. The generated command supplies `--library /absolute/catalog/library.json`. Add `--workspace /absolute/task` before the subcommand or run from the actual task directory. Mutable state is stored beneath that workspace, avoiding shared SQLite writes from sandboxed agents. No shell-profile or sandbox-permission change is required.

`install` registers immutable bundled capabilities and verifies new/unvalidated versions against disposable fixtures. It does not copy benchmark sessions, experimental versions, or another workspace's recipes. Already validated identical versions are reused without invalidating cached bindings. A persistent receipt lives next to the installed skill at `.jumpai-install.json`. Installation also atomically publishes `library.json`, containing version definitions and availability flags but no saved inputs, run logs, or checkpoint decisions. Program artifacts remain in the shared library and are integrity-checked on execution. Workspace SQLite caches and sessions persist across chats in `.jumpai/runtime/<library-identity>`. Published metadata updates merge without erasing local state; changed availability invalidates local bindings. Reinstall or run `jumpai --home CATALOG publish-library` after shared catalog changes. Updates are distributed only when published; workspace-local capabilities are not automatically promoted to the shared library.

Installation also compiles, links, and runs a small C++17 standard-library probe. It tries `CXX` (one executable path), then `c++`, `g++`, `clang++`, and MSVC `cl`. A working native toolchain selects `cpp`; otherwise it selects `python` and reports any failed probes. It never downloads a compiler. Windows MSVC requires its developer environment to be active. The catalog's SQLite `settings` table stores the `execution` record, including the compiler path and preferred runtime; publication carries this host-local setting into workspace catalogs.

Read the current setting with `jumpai --library LIBRARY settings`. To switch explicitly, run `jumpai --home CATALOG settings --prefer python` (or `cpp` after successful detection), then `jumpai --home CATALOG publish-library`. Reinstallation probes again and resets the preference from the detected capability. Reinstall on another machine or after a compiler is removed; saved compiler paths are local to the installation host. Existing Python programs stay Python; a C++ program needs a declared Python fallback to run when C++ is disabled or unavailable.

The installed skill includes the background native-build worker and macOS/BSD dependency watcher. Binary caches stay in each task's writable runtime catalog, without writes to the shared library. Warm hits reuse compiler identification and dependency watches; unsupported hosts retain full dependency checks. Watcher startup or changed source/toolchains use Python fallback while validation or building proceeds. Native-only pending runs return promptly. The watcher uses private temporary files for communication, keeps at most four dependency sets, and exits after 60 idle seconds; no system service is installed. Build-worker priority is lowered when permitted, and any host restriction is recorded in build status. Reinstall after upgrading to refresh startup guidance.

`doctor` checks the skill link/runtime, managed startup block, Codex override precedence, disabled-skill configuration, instruction byte limit, and seeded capability availability. It prints the current startup guidance as well and reads the published library without opening the shared SQLite database. It is a structural check, not proof that an arbitrary agent obeyed instructions. It cannot detect every conflicting project instruction or remote host setting.

The installer preserves unrelated instruction text, refuses to overwrite an unrelated skill, and reconciles its own block when the effective Codex instruction file changes. Reinstall to refresh the managed setup after upgrading. Remove it with:

```bash
./install.sh uninstall
```

Uninstall removes the managed skill link and instruction block and retains the catalog and unrelated instructions. A locally edited managed block or replaced skill is reported instead of silently deleted. If the checkout was moved, reinstall from its new location after removing/reconciling the old link; do not leave dangling skill links.

## Other agent hosts

Use the same lifecycle with that host's real discovery directory and startup instruction file:

```bash
./install.sh install --host generic \
  --skills-dir /absolute/host/skills \
  --instructions /absolute/host/startup-instructions.md \
  --catalog /absolute/shared/jumpai
```

Supply the same discovery flags to `plan`, `doctor`, and `uninstall`. The host must actually load those locations. This adapter does not claim that all tools read `AGENTS.md`, and does not edit another tool's settings implicitly. Install separately on remote machines or containers; filesystem paths and permissions do not transfer across hosts.

## New chats and delegated agents

Fresh Codex sessions load global instructions, while the skill remains implicitly discoverable. Start a new chat/session to pick up startup instruction changes; restart the app if the skill is absent. Existing chats may retain their original startup context.

For delegated unittest diagnosis, the startup block requires `--library LIBRARY --workspace CHILD_WORKSPACE handoff --prepare --read-only --source RELATIVE_FILE --tests '["unittest_id"]'`. Preparation runs checks once and returns `child_assignment` containing their counts and checkpoint evidence. Pass that assignment unchanged to a fresh child, which returns a decision without tools. The authorized parent waits without short polling, reviews the decision, and appends `--decision JSON` to the returned `resume_command`. Resume applies the guarded repair, runs final checks, and reports. A healthy preparation completes without a child. If evidence is insufficient, the child identifies the specific missing fact; the parent retrieves only that context. Omitting `--prepare` retains the command-card interface for other callers. Host/user constraints and explicit comparison-control exceptions still apply.

A global catalog can be readable while its SQLite writes are forbidden. The published JSON library and workspace runtime address that access failure using ordinary permitted filesystem operations; they do not request elevated execution or change sandbox policy. The workspace must still be writable.

This makes reuse the installed default, not a hard runtime interceptor: a skill cannot force every model/tool call, override user requests, or make a remote filesystem available. Audit run history when checking actual adoption. Do not add a mandatory startup tool call on every trivial chat.

Official mechanisms: [Codex skill discovery](https://learn.chatgpt.com/docs/build-skills), [global instruction loading](https://learn.chatgpt.com/docs/agent-configuration/agents-md).
