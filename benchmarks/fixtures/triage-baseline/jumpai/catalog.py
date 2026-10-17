"""SQLite metadata, immutable artifacts, full-text discovery, and resolution cache."""

import hashlib
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time

from .schema import JumpError, canonical, check_schema, parse_json

NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,79}$")
VERSION = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.+-]{0,79}$")

DDL = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
INSERT OR IGNORE INTO meta VALUES ('schema_version', 1), ('revision', 0);
CREATE TABLE IF NOT EXISTS programs (
    name TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('program','workflow'))
);
CREATE TABLE IF NOT EXISTS versions (
    id TEXT PRIMARY KEY, name TEXT NOT NULL REFERENCES programs(name),
    version TEXT NOT NULL, manifest TEXT NOT NULL, digest TEXT NOT NULL,
    artifact TEXT, validated INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL,
    UNIQUE(name, version)
);
CREATE TABLE IF NOT EXISTS workflow_steps (
    workflow_id TEXT NOT NULL REFERENCES versions(id), position INTEGER NOT NULL,
    step_name TEXT NOT NULL, target_id TEXT NOT NULL REFERENCES versions(id),
    bindings TEXT NOT NULL, PRIMARY KEY(workflow_id, position)
);
CREATE VIRTUAL TABLE IF NOT EXISTS discovery USING fts5(id UNINDEXED, name, purpose, use_cases, tags);
CREATE TABLE IF NOT EXISTS resolutions (
    query TEXT NOT NULL, scope TEXT NOT NULL, context TEXT NOT NULL,
    target_id TEXT NOT NULL REFERENCES versions(id), revision INTEGER NOT NULL,
    hits INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL,
    PRIMARY KEY(query, scope, context)
);
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY, target_id TEXT NOT NULL REFERENCES versions(id),
    parent_id INTEGER REFERENCES runs(id), started REAL NOT NULL,
    duration_ms REAL, success INTEGER, error TEXT
);
"""

RECIPE_DDL = """CREATE TABLE IF NOT EXISTS recipes (
    name TEXT NOT NULL, scope TEXT NOT NULL, context TEXT NOT NULL,
    target_id TEXT NOT NULL REFERENCES versions(id), inputs TEXT NOT NULL,
    revision INTEGER NOT NULL, hits INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL,
    PRIMARY KEY(name, scope, context)
)"""


def query_key(query):
    return " ".join(query.casefold().split())


def strings(value, name):
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise JumpError(f"{name} must be an array of nonempty strings")


def pointer(value, path):
    if path == "":
        return value
    if not isinstance(path, str) or not path.startswith("/"):
        raise JumpError("JSON pointers must be empty or start with /")
    try:
        for part in path[1:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if isinstance(value, list):
                if not re.fullmatch(r"0|[1-9][0-9]*", part):
                    raise ValueError("invalid array index")
                value = value[int(part)]
            elif isinstance(value, dict):
                value = value[part]
            else:
                raise TypeError("not a container")
        return value
    except (KeyError, ValueError, IndexError, TypeError) as exc:
        raise JumpError(f"JSON pointer does not resolve: {path}") from exc


class Catalog:
    def __init__(self, home):
        self.home = Path(home).expanduser().resolve()
        self.home.mkdir(parents=True, exist_ok=True)
        self.artifacts = self.home / "artifacts"
        self.artifacts.mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.home / "catalog.sqlite3", timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        if self.db.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone():
            version = self.db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
            if not version or version[0] not in (1, 2):
                self.db.close()
                raise JumpError("Unsupported catalog schema version")
        self.db.executescript(DDL)
        with self.db:
            self.db.execute(RECIPE_DDL)
            self.db.execute("UPDATE meta SET value=2 WHERE key='schema_version'")

    def close(self):
        self.db.close()

    @property
    def revision(self):
        return self.db.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0]

    def bump(self):
        self.db.execute("UPDATE meta SET value=value+1 WHERE key='revision'")

    def get(self, target, *, enabled=True):
        row = self.db.execute("SELECT * FROM versions WHERE id=?", (target,)).fetchone()
        if row is None:
            raise JumpError(f"Unknown version {target}; use an explicit name@version")
        if enabled and not row["enabled"]:
            raise JumpError(f"Version disabled: {target}")
        return row

    def contract(self, target):
        row = self.get(target)
        return {"id": target, "validated": bool(row["validated"]),
                "digest": row["digest"], "manifest": parse_json(row["manifest"])}

    def register(self, filename):
        filename = Path(filename).resolve()
        m = parse_json(filename.read_text())
        if not isinstance(m, dict):
            raise JumpError("Manifest must be an object")
        allowed = {"name", "version", "kind", "purpose", "use_cases", "tags", "input_schema", "output_schema",
                   "runtime", "entrypoint", "files", "arguments", "timeout_seconds", "steps", "output"}
        if set(m) - allowed:
            raise JumpError(f"Unknown manifest fields: {sorted(set(m) - allowed)}")
        for key, pattern in (("name", NAME), ("version", VERSION)):
            if not isinstance(m.get(key), str) or not pattern.fullmatch(m[key]):
                raise JumpError(f"Invalid {key}")
        kind = m.get("kind", "program")
        m["kind"] = kind
        if kind not in {"program", "workflow"}:
            raise JumpError("kind must be program or workflow")
        if not isinstance(m.get("purpose"), str) or not m["purpose"].strip():
            raise JumpError("purpose is required")
        for key in ("use_cases", "tags"):
            strings(m.get(key, []), key)
        for key in ("input_schema", "output_schema"):
            check_schema(m.get(key), key)
        target = f"{m['name']}@{m['version']}"
        files = {}
        steps = []
        if kind == "program":
            if "steps" in m or "output" in m:
                raise JumpError("Program manifests cannot contain workflow fields")
            if m.get("runtime") != "python":
                raise JumpError("This release supports runtime=python; other backends are reserved")
            strings(m.get("files"), "files")
            strings(m.get("arguments", []), "arguments")
            if len(m["files"]) != len(set(m["files"])):
                raise JumpError("Duplicate artifact files")
            if m.get("entrypoint") not in m["files"]:
                raise JumpError("entrypoint must be included in files")
            timeout = m.get("timeout_seconds", 30)
            if type(timeout) not in (int, float) or not 0 < timeout <= 3600:
                raise JumpError("timeout_seconds must be > 0 and <= 3600")
            for rel in m["files"]:
                p = Path(rel)
                if p.is_absolute() or '..' in p.parts or p.as_posix() != rel or rel == '.':
                    raise JumpError(f"Artifact path must be a normalized relative file: {rel}")
                source = filename.parent / p
                if source.is_symlink() or not source.resolve().is_relative_to(filename.parent):
                    raise JumpError(f"Artifact escapes bundle or is a symlink: {rel}")
                if not source.is_file():
                    raise JumpError(f"Missing artifact: {rel}")
                files[rel] = source.read_bytes()
        else:
            if set(m) & {"runtime", "entrypoint", "files", "arguments", "timeout_seconds"}:
                raise JumpError("Workflow manifests cannot contain program fields")
            if m["input_schema"]["type"] != "object":
                raise JumpError("Workflow input must be an object")
            if not isinstance(m.get("steps"), list) or not 1 <= len(m["steps"]) <= 100:
                raise JumpError("Workflow needs 1–100 steps")
            seen = set()
            for step in m["steps"]:
                if not isinstance(step, dict) or set(step) != {"name", "target", "bindings"}:
                    raise JumpError("Each step requires only name, target, bindings")
                name = step["name"]
                if not isinstance(name, str) or not NAME.fullmatch(name) or name in seen:
                    raise JumpError("Step names must be valid and unique")
                if not isinstance(step["target"], str):
                    raise JumpError("Step target must be name@version")
                row = self.get(step["target"])
                dep = parse_json(row["manifest"])
                if dep["kind"] != "program":
                    raise JumpError("This release composes programs only; nested workflows are not supported")
                if dep["input_schema"]["type"] != "object":
                    raise JumpError("Step programs must accept objects")
                if not isinstance(step["bindings"], dict):
                    raise JumpError("Step bindings must be an object")
                for binding in step["bindings"].values():
                    self.check_binding(binding, seen)
                if set(dep["input_schema"].get("required", [])) - step["bindings"].keys():
                    raise JumpError(f"Missing required bindings for step {name}")
                steps.append(step)
                seen.add(name)
            self.check_binding(m.get("output"), seen)
        digest = hashlib.sha256(canonical({"manifest": m, "files": {
            rel: hashlib.sha256(data).hexdigest() for rel, data in files.items()}}).encode()).hexdigest()
        artifact = None
        if kind == "program":
            dest = self.artifacts / digest
            staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=self.artifacts))
            try:
                for rel, data in files.items():
                    out = staging / rel
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(data)
                if not dest.exists():
                    try:
                        staging.rename(dest)
                    except FileExistsError:
                        pass
                artifact = str(dest)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            existing = self.db.execute("SELECT digest FROM versions WHERE id=?", (target,)).fetchone()
            if existing:
                if existing[0] != digest:
                    raise JumpError(f"Immutable version already exists: {target}; register a new version")
                return {"id": target, "registered": False, "digest": digest}
            name_row = self.db.execute("SELECT kind FROM programs WHERE name=?", (m["name"],)).fetchone()
            if name_row and name_row[0] != kind:
                raise JumpError("A capability cannot change between program and workflow")
            self.db.execute("INSERT OR IGNORE INTO programs VALUES (?,?)", (m["name"], kind))
            self.db.execute("INSERT INTO versions(id,name,version,manifest,digest,artifact,created) VALUES (?,?,?,?,?,?,?)",
                            (target, m["name"], m["version"], canonical(m), digest, artifact, time.time()))
            self.db.execute("INSERT INTO discovery VALUES (?,?,?,?,?)", (target, m["name"], m["purpose"],
                            ' '.join(m.get("use_cases", [])), ' '.join(m.get("tags", []))))
            for index, step in enumerate(steps):
                self.db.execute("INSERT INTO workflow_steps VALUES (?,?,?,?,?)",
                                (target, index, step["name"], step["target"], canonical(step["bindings"])))
            self.bump()
        return {"id": target, "registered": True, "digest": digest}

    @staticmethod
    def check_binding(binding, seen):
        if not isinstance(binding, dict):
            raise JumpError("Bindings must be objects")
        if set(binding) == {"value"}:
            return
        if set(binding) == {"input"}:
            path = binding["input"]
        elif set(binding) == {"step", "pointer"}:
            if not isinstance(binding["step"], str) or binding["step"] not in seen:
                raise JumpError("Step bindings can reference only preceding steps")
            path = binding["pointer"]
        else:
            raise JumpError("Binding must be {input: pointer}, {step: name, pointer: pointer}, or {value: literal}")
        if not isinstance(path, str) or (path != '' and not path.startswith('/')) or re.search(r"~(?![01])", path):
            raise JumpError("Invalid JSON pointer in binding")

    def search(self, query, limit=5):
        if not 1 <= limit <= 50:
            raise JumpError("limit must be between 1 and 50")
        tokens = re.findall(r"\w+", query, flags=re.UNICODE)[:20]
        if not tokens:
            return []
        expression = ' OR '.join('"' + t + '"' for t in tokens)
        rows = self.db.execute("""SELECT v.id,v.manifest,v.validated FROM discovery
            JOIN versions v ON v.id=discovery.id WHERE discovery MATCH ? AND v.enabled=1
            ORDER BY v.validated DESC,bm25(discovery),v.id LIMIT ?""", (expression, limit)).fetchall()
        return [{"id": r["id"], "purpose": parse_json(r["manifest"])["purpose"],
                 "validated": bool(r["validated"])} for r in rows]

    def available(self, target):
        row = self.get(target)
        if not row["validated"]:
            raise JumpError(f"Version is experimental: {target}; verify it first or explicitly allow experimental execution")
        for dep in self.db.execute("SELECT target_id FROM workflow_steps WHERE workflow_id=?", (target,)):
            self.available(dep[0])
        return row

    def remember(self, query, target, scope, context="", expected_revision=None):
        key = query_key(query)
        if not key:
            raise JumpError("Resolution query cannot be empty")
        scope = str(Path(scope).resolve())
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.available(target)
            if expected_revision is not None and expected_revision != self.revision:
                raise JumpError("Catalog changed during execution; run succeeded but resolution was not cached")
            self.db.execute("""INSERT INTO resolutions(query,scope,context,target_id,revision,created)
                VALUES (?,?,?,?,?,?) ON CONFLICT(query,scope,context) DO UPDATE SET
                target_id=excluded.target_id,revision=excluded.revision,hits=0,created=excluded.created""",
                (key, scope, context, target, self.revision, time.time()))
        return {"query": key, "id": target, "scope": scope, "context": context}

    def resolve(self, query, scope, context="", limit=5):
        key = query_key(query)
        if not key:
            raise JumpError("Resolution query cannot be empty")
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM resolutions WHERE query=? AND scope=? AND context=?",
                                  (key, str(Path(scope).resolve()), context)).fetchone()
            if row and row["revision"] == self.revision:
                try:
                    self.available(row["target_id"])
                except JumpError:
                    pass
                else:
                    self.db.execute("UPDATE resolutions SET hits=hits+1 WHERE query=? AND scope=? AND context=?",
                                    (key, row["scope"], context))
                    return {"cache_hit": True, "id": row["target_id"], "contract": self.contract(row["target_id"])}
        return {"cache_hit": False, "candidates": self.search(query, limit)}

    def set_enabled(self, target, enabled):
        self.get(target, enabled=False)
        with self.db:
            self.db.execute("UPDATE versions SET enabled=? WHERE id=?", (int(enabled), target))
            self.bump()
        return {"id": target, "enabled": enabled}

    def save_recipe(self, name, target, inputs, scope, context, expected_revision):
        """Called after successful execution; argument persistence is explicit opt-in."""
        if not NAME.fullmatch(name):
            raise JumpError("Recipe name must match [a-z0-9][a-z0-9_.-]{0,79}")
        scope = str(Path(scope).resolve())
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.available(target)
            if expected_revision != self.revision:
                raise JumpError("Catalog changed during execution; run succeeded but recipe was not saved")
            self.db.execute("""INSERT INTO recipes(name,scope,context,target_id,inputs,revision,created)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(name,scope,context) DO UPDATE SET
                target_id=excluded.target_id,inputs=excluded.inputs,revision=excluded.revision,
                hits=0,created=excluded.created""",
                (name, scope, context, target, canonical(inputs), self.revision, time.time()))
        return {"name": name, "id": target, "scope": scope, "context": context}

    def recipe(self, name, scope, context=""):
        """Direct exact lookup: no discovery and no contract returned to the agent."""
        scope = str(Path(scope).resolve())
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row = self.db.execute("SELECT * FROM recipes WHERE name=? AND scope=? AND context=?",
                                  (name, scope, context)).fetchone()
            if row is None or row["revision"] != self.revision:
                return None
            self.available(row["target_id"])
            self.db.execute("UPDATE recipes SET hits=hits+1 WHERE name=? AND scope=? AND context=?",
                            (name, scope, context))
            return {"id": row["target_id"], "input": parse_json(row["inputs"])}

    def set_validated(self, target, validated):
        with self.db:
            self.db.execute("UPDATE versions SET validated=? WHERE id=?", (int(validated), target))
            self.bump()

    def stats(self):
        result = {table: self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                  for table in ("programs", "versions", "workflow_steps", "resolutions", "recipes", "runs")}
        result["recipe_hits"] = self.db.execute("SELECT coalesce(sum(hits),0) FROM recipes").fetchone()[0]
        result["resolution_hits"] = self.db.execute("SELECT coalesce(sum(hits),0) FROM resolutions").fetchone()[0]
        result["successful_runs"] = self.db.execute("SELECT count(*) FROM runs WHERE success=1").fetchone()[0]
        result["revision"] = self.revision
        return result
