"""Small JSON CLI; it can be wrapped by any agent or an MCP adapter."""

import argparse
import os
import shlex
from pathlib import Path
import sqlite3
import sys

from .catalog import Catalog, NAME
from .runner import Runner
from .workflow import WorkflowSessions
from .schema import JumpError, canonical, parse_json


def parser():
    """Build the command parser without opening a catalog or executing work."""
    # Select catalog storage and the workspace before parsing the operation.
    p = argparse.ArgumentParser(prog="jumpai", description="Find, cache, and run versioned local programs")
    location = p.add_mutually_exclusive_group()
    location.add_argument("--home", help="explicit writable catalog directory (or JUMPAI_HOME)")
    location.add_argument("--library", help="read-only installed library.json; keep mutable state in the workspace")
    p.add_argument("--workspace", default=str(Path.cwd()), help="task workspace; defaults to the current directory")
    sub = p.add_subparsers(dest="command", required=True)
    usage = sub.add_parser('usage', help='read previous-turn usage and Jump AI turn averages from local Codex logs')
    usage.add_argument('--session', help='explicit Codex rollout JSONL file')
    usage.add_argument('--thread-id', help='thread UUID; defaults to CODEX_THREAD_ID')
    usage.add_argument('--sessions-dir', help='Codex sessions directory; defaults to CODEX_HOME/sessions')
    usage.add_argument('--footer', action='store_true', help='print a compact chat footer instead of JSON')
    sub.add_parser("init")
    sub.add_parser("stats")
    sub.add_parser("publish-library", help="publish definitions for sandboxed workspace runtimes")
    settings = sub.add_parser('settings', help='show execution settings or select a verified runtime')
    settings.add_argument('--prefer', choices=['python', 'cpp'])
    handoff_parser = sub.add_parser("handoff", help="prepare runnable commands for a delegated task")
    handoff_parser.add_argument("--tests", default="[]", help="JSON array of unittest IDs")
    handoff_parser.add_argument("--source", help="relative source file to diagnose")
    handoff_parser.add_argument("--prepare", action="store_true",
                                help="run diagnosis once and hand off evidence for a tool-free decision")
    mode = handoff_parser.add_mutually_exclusive_group()
    mode.add_argument("--read-only", action="store_true", default=True, dest="read_only")
    mode.add_argument("--allow-source-edits", action="store_false", dest="read_only")
    register = sub.add_parser("register")
    register.add_argument("manifest")
    search = sub.add_parser("search")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=5)
    inspect = sub.add_parser("inspect")
    inspect.add_argument("target", help="name@version")
    for command in ("resolve", "remember"):
        s = sub.add_parser(command)
        s.add_argument("query")
        if command == "remember":
            s.add_argument("target")
        s.add_argument("--scope", default=None, help="workspace directory used as the cache namespace")
        s.add_argument("--context", default="", help="exact applicability key, e.g. runtime or schema fingerprint")
    # Execution, saved recipes, and resumable session commands.
    run = sub.add_parser("run")
    target = run.add_mutually_exclusive_group(required=True)
    target.add_argument("--target", help="name@version")
    target.add_argument("--query", help="execute only an existing cached resolution; never guess on a miss")
    data = run.add_mutually_exclusive_group()
    data.add_argument("--input", default="{}", help="JSON arguments")
    data.add_argument("--input-file", help="JSON file or - for stdin")
    run.add_argument("--cwd", default=None, help="execution workspace and cache scope")
    run.add_argument("--context", default="")
    run.add_argument("--remember", help="cache this request after successful validated execution")
    run.add_argument("--save-recipe", help="explicitly persist this successful invocation's arguments under a name")
    run.add_argument("--allow-experimental", action="store_true")
    run.add_argument("--resumable", action="store_true", help="persist workflow state; return compact checkpoint/result receipts")
    jump = sub.add_parser("jump", help="execute a saved invocation directly, without discovery")
    jump.add_argument("name")
    jump.add_argument("--cwd", default=None)
    jump.add_argument("--context", default="")
    jump.add_argument("--with", dest="overrides", default="{}", help="JSON object of top-level argument replacements")
    jump.add_argument("--resumable", action="store_true")
    for command in ("resume", "status", "result"):
        session = sub.add_parser(command)
        session.add_argument("session_id")
        session.add_argument("--cwd", default=None)
        if command == "resume":
            session.add_argument("--step", required=True, help="exact pending decision name, to reject stale replies")
            session.add_argument("--decision", required=True, help="JSON decision matching the checkpoint schema")
            session.add_argument("--save-recipe", help="save original arguments if this continuation completes successfully")
            session.add_argument("--context", default="")
        elif command == "result":
            session.add_argument("--step", help="retrieve one stored step instead of the completed output")
    verify = sub.add_parser("verify")
    verify.add_argument("target")
    verify.add_argument("--cases", required=True)
    verify.add_argument("--cwd", default=None)
    for command in ("disable", "enable"):
        s = sub.add_parser(command)
        s.add_argument("target")
    return p


def recipe_card(catalog, name, target, inputs, cwd, context, revision):
    """Persist an authorized recipe and return a shell-quoted replay command.

    Reject stale revisions or unavailable targets through the catalog API.
    """
    card = catalog.save_recipe(name, target, inputs, cwd, context, revision)
    launcher = Path(__file__).resolve().parents[1] / "skills/jumpai/scripts/jumpai.py"
    invocation = [sys.executable, str(launcher)] if launcher.is_file() else [sys.executable, "-m", "jumpai"]
    manifest = parse_json(catalog.get(target)["manifest"])
    resumable = manifest["kind"] == "workflow" and any("decision" in s for s in manifest["steps"])
    card["command"] = shlex.join(invocation + ["--home", str(catalog.home), "jump", name,
        "--cwd", str(Path(cwd).resolve()), "--context", context] + (["--resumable"] if resumable else []))
    return card


def main(argv=None):
    """Dispatch CLI arguments, emit JSON, and always close the opened catalog.

    Return 0 for success, 2 for operational errors, 3 for a cache miss, or 130
    for interruption. Commands may execute programs with caller permissions.
    """
    args = parser().parse_args(argv)
    # Usage inspection requires neither a writable catalog nor a program execution.
    if args.command == 'usage':
        from .usage import footer, report
        result = report(args.session, args.thread_id, args.sessions_dir)
        print(footer(result) if args.footer else canonical(result))
        return 0 if 'unavailable' not in result else 2
    catalog = None
    try:
        from .workspace import open_workspace, handoff, publish
        if hasattr(args, "cwd") and args.cwd is None:
            args.cwd = args.workspace
        if hasattr(args, "scope") and args.scope is None:
            args.scope = args.workspace
        workspace = getattr(args, "cwd", None) or getattr(args, "scope", None) or args.workspace
        if args.library and args.command == "publish-library":
            raise JumpError("Publishing requires an explicit writable --home, not --library")
        # Keep shared library metadata read-only and mutable task state local.
        if args.library:
            catalog = open_workspace(args.library, workspace)
        else:
            home = args.home or os.environ.get("JUMPAI_HOME", str(Path.home() / ".local/share/jumpai"))
            catalog = Catalog(home)

        # Dispatch only after storage and workspace selection have succeeded.
        command = args.command
        if command == "init":
            result = {"home": str(catalog.home), "schema_version": 3}
        elif command == "publish-library":
            result = {"library": publish(catalog.home)}
        elif command == "handoff":
            result = handoff(catalog, args.library, workspace, parse_json(args.tests),
                             args.source, args.read_only, prepare=args.prepare)
        elif command == "stats":
            result = catalog.stats()
        elif command == 'settings':
            result = catalog.execution_settings()
            if args.prefer:
                if args.library:
                    raise JumpError('Change settings through writable --home, then publish-library')
                result['preferred_runtime'] = args.prefer
                catalog.set_execution_settings(result)
        elif command == "register":
            result = catalog.register(args.manifest)
        elif command == "search":
            result = {"candidates": catalog.search(args.query, args.limit)}
        elif command == "inspect":
            result = catalog.contract(args.target)
        elif command == "resolve":
            result = catalog.resolve(args.query, args.scope, args.context)
        elif command == "remember":
            result = catalog.remember(args.query, args.target, args.scope, args.context)
        elif command == "verify":
            result = Runner(catalog, args.cwd).verify(args.target, args.cases)
        elif command in {"disable", "enable"}:
            result = catalog.set_enabled(args.target, command == "enable")
        elif command in {"resume", "status", "result"}:
            sessions = WorkflowSessions(Runner(catalog, args.cwd))
            if command == "status":
                result = sessions.status(args.session_id)
            elif command == "result":
                result = sessions.result(args.session_id, step=args.step)
            else:
                row, state = sessions._load(args.session_id)
                if args.save_recipe is not None:
                    if not NAME.fullmatch(args.save_recipe) or state["experimental"]:
                        raise JumpError("Saving a recipe requires a valid name and non-experimental execution")
                result = sessions.resume(args.session_id, parse_json(args.decision), step=args.step)
                if args.save_recipe:
                    if result["status"] == "completed":
                        try:
                            result["recipe"] = recipe_card(catalog, args.save_recipe, row["target_id"], state["inputs"],
                                args.cwd, args.context, state["revision"])
                        except JumpError as exc:
                            result["recipe_warning"] = str(exc)
                    else:
                        result["recipe_warning"] = "Workflow paused again; save the recipe on its completing resume"
        elif command == "jump":
            saved = catalog.recipe(args.name, args.cwd, args.context)
            if saved is None:
                print(canonical({"cache_hit": False, "error": "Recipe missing or stale for this scope/context; select a validated target and run with --save-recipe"}))
                return 3
            overrides = parse_json(args.overrides)
            if not isinstance(overrides, dict):
                raise JumpError("--with must be a JSON object")
            inputs = saved["input"]
            if overrides:
                if not isinstance(inputs, dict):
                    raise JumpError("--with requires a recipe with object inputs")
                inputs = {**inputs, **overrides}
            runner = Runner(catalog, args.cwd)
            result = WorkflowSessions(runner).start(saved["id"], inputs) if args.resumable else runner.run(saved["id"], inputs)
            result["cache_hit"] = True
        else:
            if (args.remember or args.save_recipe) and args.allow_experimental:
                raise JumpError("Experimental executions cannot be remembered")
            if args.save_recipe is not None and not NAME.fullmatch(args.save_recipe):
                raise JumpError("Recipe name must match [a-z0-9][a-z0-9_.-]{0,79}")
            if args.remember is not None and not args.remember.strip():
                raise JumpError("Resolution query cannot be empty")
            revision = catalog.revision
            target = args.target
            cache_hit = False
            if args.query:
                resolution = catalog.resolve(args.query, args.cwd, args.context)
                if not resolution["cache_hit"]:
                    print(canonical({"error": "No current cached resolution; select and verify a candidate, then remember it", **resolution}))
                    return 3
                target = resolution["id"]
                cache_hit = True
            if args.input_file:
                raw = sys.stdin.read() if args.input_file == "-" else Path(args.input_file).read_text()
            else:
                raw = args.input
            inputs = parse_json(raw)
            runner = Runner(catalog, args.cwd)
            result = (WorkflowSessions(runner).start(target, inputs, allow_experimental=args.allow_experimental) if args.resumable
                      else runner.run(target, inputs, allow_experimental=args.allow_experimental))
            result["cache_hit"] = cache_hit
            completed = not args.resumable or result["status"] == "completed"
            if args.remember:
                if completed:
                    try:
                        result["remembered"] = catalog.remember(args.remember, target, args.cwd, args.context, revision)
                    except JumpError as exc:
                        # Do not report a successful side-effecting run as a failed execution.
                        result["cache_warning"] = str(exc)
                else:
                    result["cache_warning"] = "Workflow paused; no resolution saved. Save a recipe on the completing resume"
            if args.save_recipe:
                if completed:
                    try:
                        result["recipe"] = recipe_card(catalog, args.save_recipe, target, inputs, args.cwd, args.context, revision)
                    except JumpError as exc:
                        result["recipe_warning"] = str(exc)
                else:
                    result["recipe_warning"] = "Workflow paused; use --save-recipe on the completing resume"
        # Mark actual successful execution/checkpoint returns, excluding discovery and telemetry.
        if command in {'run', 'jump', 'resume', 'verify'} or (command == 'handoff' and args.prepare):
            result['jumpai_execution'] = True
        if args.library:
            result["catalog_home"] = str(catalog.home)
        print(canonical(result))
        return 0
    except (JumpError, OSError, sqlite3.Error) as exc:
        print(canonical({"error": str(exc)}), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print(canonical({"error": "Interrupted"}), file=sys.stderr)
        return 130
    finally:
        if catalog:
            catalog.close()
