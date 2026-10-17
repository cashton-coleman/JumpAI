"""JSON stdin/stdout execution. Programs run with the caller's OS permissions."""

import hashlib
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time

from .catalog import pointer
from .schema import JumpError, canonical, parse_json, validate

MAX_BYTES = 2 * 1024 * 1024


class Runner:
    def __init__(self, catalog, cwd=None):
        self.catalog = catalog
        self.cwd = Path(cwd or Path.cwd()).resolve()
        if not self.cwd.is_dir():
            raise JumpError(f"Working directory does not exist: {self.cwd}")

    def run(self, target, inputs, *, allow_experimental=False, parent_id=None):
        row = self.catalog.get(target)
        if not allow_experimental:
            self.catalog.available(target)
        manifest = parse_json(row["manifest"])
        validate(inputs, manifest["input_schema"])
        if len(canonical(inputs).encode()) > MAX_BYTES:
            raise JumpError("Input exceeds 2 MiB; pass file paths for large data")
        started = time.monotonic()
        with self.catalog.db:
            run_id = self.catalog.db.execute("INSERT INTO runs(target_id,parent_id,started) VALUES (?,?,?)",
                                           (target, parent_id, time.time())).lastrowid
        try:
            if manifest["kind"] == "program":
                output = self._program(row, manifest, inputs)
            else:
                results = {}
                steps = self.catalog.db.execute("SELECT * FROM workflow_steps WHERE workflow_id=? ORDER BY position", (target,)).fetchall()
                for step in steps:
                    bound = {key: self._bind(binding, inputs, results)
                             for key, binding in parse_json(step["bindings"]).items()}
                    try:
                        result = self.run(step["target_id"], bound, allow_experimental=allow_experimental, parent_id=run_id)
                    except JumpError as exc:
                        raise JumpError(f"Step {step['step_name']} ({step['target_id']}): {exc}") from exc
                    results[step["step_name"]] = result["output"]
                output = self._bind(manifest["output"], inputs, results)
            validate(output, manifest["output_schema"], "output")
            elapsed = round((time.monotonic() - started) * 1000, 3)
            with self.catalog.db:
                self.catalog.db.execute("UPDATE runs SET duration_ms=?,success=1 WHERE id=?", (elapsed, run_id))
            return {"id": target, "run_id": run_id, "duration_ms": elapsed, "output": output}
        except BaseException as exc:
            with self.catalog.db:
                # Persist error category only; arguments, results, and stderr can contain private data.
                self.catalog.db.execute("UPDATE runs SET duration_ms=?,success=0,error=? WHERE id=?",
                                        ((time.monotonic() - started) * 1000, type(exc).__name__, run_id))
            raise

    @staticmethod
    def _bind(binding, inputs, results):
        if "value" in binding:
            return binding["value"]
        if "input" in binding:
            return pointer(inputs, binding["input"])
        return pointer(results[binding["step"]], binding["pointer"])

    @staticmethod
    def _stop(process):
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        elif process.poll() is None:
            process.kill()
        process.wait()

    def _program(self, row, manifest, inputs):
        artifact = Path(row["artifact"])
        if artifact.is_symlink() or not artifact.is_dir():
            raise JumpError("Missing or invalid artifact directory")
        actual_files = {p.relative_to(artifact).as_posix() for p in artifact.rglob('*') if not p.is_dir()}
        if actual_files != set(manifest["files"]) or any(p.is_symlink() for p in artifact.rglob('*')):
            raise JumpError("Artifact integrity check failed: bundle file inventory changed")
        hashes = {}
        for rel in manifest["files"]:
            file = artifact / rel
            if file.is_symlink() or not file.resolve().is_relative_to(artifact) or not file.is_file():
                raise JumpError(f"Missing or invalid registered artifact: {rel}")
            hashes[rel] = hashlib.sha256(file.read_bytes()).hexdigest()
        digest = hashlib.sha256(canonical({"manifest": manifest, "files": hashes}).encode()).hexdigest()
        if digest != row["digest"]:
            raise JumpError("Artifact integrity check failed; restore the original bundle or register a new version")
        command = [sys.executable, "-B", str(artifact / manifest["entrypoint"]), *manifest.get("arguments", [])]
        deadline = time.monotonic() + manifest.get("timeout_seconds", 30)
        with tempfile.TemporaryFile() as stdin, tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            stdin.write(canonical(inputs).encode())
            stdin.seek(0)
            try:
                process = subprocess.Popen(command, stdin=stdin, stdout=stdout, stderr=stderr,
                                           cwd=self.cwd, start_new_session=(os.name == "posix"))
            except OSError as exc:
                raise JumpError(f"Could not start program: {exc}") from exc
            try:
                while True:
                    code = process.poll()
                    if os.fstat(stdout.fileno()).st_size > MAX_BYTES or os.fstat(stderr.fileno()).st_size > MAX_BYTES:
                        raise JumpError("Program output exceeded 2 MiB; write large results to an artifact file")
                    if code is not None:
                        break
                    if time.monotonic() >= deadline:
                        raise JumpError(f"Program exceeded timeout of {manifest.get('timeout_seconds', 30)} seconds")
                    time.sleep(0.01)
                stderr.seek(0)
                error_text = stderr.read(4096).decode("utf-8", errors="replace")
                if code != 0:
                    raise JumpError(f"Program exited with code {code}: {error_text.strip()}")
                stdout.seek(0)
                try:
                    return parse_json(stdout.read(MAX_BYTES + 1).decode("utf-8"))
                except UnicodeError as exc:
                    raise JumpError("Program stdout must be UTF-8 JSON") from exc
            finally:
                # Also clean up background descendants after a successful parent exits on POSIX.
                self._stop(process)

    def verify(self, target, cases_file):
        cases = parse_json(Path(cases_file).read_text())
        if not isinstance(cases, list) or not cases:
            raise JumpError("Verification needs a nonempty array of {input, output} cases")
        for case in cases:
            if not isinstance(case, dict) or set(case) != {"input", "output"}:
                raise JumpError("Each verification case requires exactly input and output")
        self.catalog.get(target)
        self.catalog.set_validated(target, False)
        for index, case in enumerate(cases):
            result = self.run(target, case["input"], allow_experimental=True)
            if canonical(result["output"]) != canonical(case["output"]):
                raise JumpError(f"Verification case {index + 1} failed: output did not match expected JSON")
        self.catalog.set_validated(target, True)
        return {"id": target, "validated": True, "cases_passed": len(cases)}
