"""Small JSON CLI; it can be wrapped by any agent or an MCP adapter."""

import argparse
import os
import shlex
from pathlib import Path
import sqlite3
import sys

from .catalog import Catalog, NAME
from .runner import Runner
from .schema import JumpError, canonical, parse_json


def parser():
    p = argparse.ArgumentParser(prog="jumpai", description="Find, cache, and run versioned local programs")
    p.add_argument("--home", default=os.environ.get("JUMPAI_HOME", str(Path.home() / ".local/share/jumpai")),
                   help="catalog and artifact directory (or JUMPAI_HOME)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("stats")
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
        s.add_argument("--scope", default=str(Path.cwd()), help="workspace directory used as the cache namespace")
        s.add_argument("--context", default="", help="exact applicability key, e.g. runtime or schema fingerprint")
    run = sub.add_parser("run")
    target = run.add_mutually_exclusive_group(required=True)
    target.add_argument("--target", help="name@version")
    target.add_argument("--query", help="execute only an existing cached resolution; never guess on a miss")
    data = run.add_mutually_exclusive_group()
    data.add_argument("--input", default="{}", help="JSON arguments")
    data.add_argument("--input-file", help="JSON file or - for stdin")
    run.add_argument("--cwd", default=str(Path.cwd()), help="execution workspace and cache scope")
    run.add_argument("--context", default="")
    run.add_argument("--remember", help="cache this request after successful validated execution")
    run.add_argument("--save-recipe", help="explicitly persist this successful invocation's arguments under a name")
    run.add_argument("--allow-experimental", action="store_true")
    jump = sub.add_parser("jump", help="execute a saved invocation directly, without discovery")
    jump.add_argument("name")
    jump.add_argument("--cwd", default=str(Path.cwd()))
    jump.add_argument("--context", default="")
    jump.add_argument("--with", dest="overrides", default="{}", help="JSON object of top-level argument replacements")
    verify = sub.add_parser("verify")
    verify.add_argument("target")
    verify.add_argument("--cases", required=True)
    verify.add_argument("--cwd", default=str(Path.cwd()))
    for command in ("disable", "enable"):
        s = sub.add_parser(command)
        s.add_argument("target")
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    catalog = None
    try:
        catalog = Catalog(args.home)
        command = args.command
        if command == "init":
            result = {"home": str(catalog.home), "schema_version": 2}
        elif command == "stats":
            result = catalog.stats()
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
            result = Runner(catalog, args.cwd).run(saved["id"], inputs)
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
            result = Runner(catalog, args.cwd).run(target, inputs, allow_experimental=args.allow_experimental)
            result["cache_hit"] = cache_hit
            if args.remember:
                try:
                    result["remembered"] = catalog.remember(args.remember, target, args.cwd, args.context, revision)
                except JumpError as exc:
                    # Do not report a successful side-effecting run as a failed execution.
                    result["cache_warning"] = str(exc)
            if args.save_recipe:
                try:
                    card = catalog.save_recipe(args.save_recipe, target, inputs, args.cwd, args.context, revision)
                    launcher = Path(__file__).resolve().parents[1] / "skills/jumpai/scripts/jumpai.py"
                    invocation = [sys.executable, str(launcher)] if launcher.is_file() else [sys.executable, "-m", "jumpai"]
                    card["command"] = shlex.join(invocation + ["--home", str(catalog.home), "jump", args.save_recipe,
                        "--cwd", str(Path(args.cwd).resolve()), "--context", args.context])
                    result["recipe"] = card
                except JumpError as exc:
                    result["recipe_warning"] = str(exc)
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
