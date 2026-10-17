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

# Bound in-memory inputs and captured subprocess output.
MAX_BYTES = 2 * 1024 * 1024


class Runner:
    """Execute catalog capabilities and record outcomes without storing payloads."""

    def __init__(self, catalog, cwd=None):
        """Bind an open catalog to an existing workspace; reject missing directories."""
        self.catalog = catalog
        self.cwd = Path(cwd or Path.cwd()).resolve()
        if not self.cwd.is_dir():
            raise JumpError(f"Working directory does not exist: {self.cwd}")

    def run(self, target, inputs, *, allow_experimental=False, parent_id=None):
        """Run a pinned capability and return its output plus execution metadata.

        Validate contracts, record success or failure, and propagate errors.
        Programs may change files using the caller's operating-system permissions.
        """
        # Validate the request before creating a run or executing user code.
        row = self.catalog.get(target)
        if not allow_experimental:
            self.catalog.available(target)
        manifest = parse_json(row["manifest"])
        if manifest["kind"] == "workflow" and any("decision" in step for step in manifest["steps"]):
            raise JumpError("Workflows with decision steps require --resumable")
        validate(inputs, manifest["input_schema"])
        if len(canonical(inputs).encode()) > MAX_BYTES:
            raise JumpError("Input exceeds 2 MiB; pass file paths for large data")
        # Link child runs to their workflow without persisting input payloads.
        started = time.monotonic()
        with self.catalog.db:
            run_id = self.catalog.db.execute("INSERT INTO runs(target_id,parent_id,started) VALUES (?,?,?)",
                                           (target, parent_id, time.time())).lastrowid
        try:
            native_steps = {}
            if manifest["kind"] == "program":
                output = self._program(row, manifest, inputs)
            else:
                results = {}
                for step in manifest["steps"]:
                    if not self._condition(step, inputs, results):
                        results[step["name"]] = self._bind(step.get("otherwise", {"value": None}), inputs, results)
                        continue
                    bound = {key: self._bind(binding, inputs, results)
                             for key, binding in step["bindings"].items()}
                    try:
                        result = self.run(step["target"], bound, allow_experimental=allow_experimental, parent_id=run_id)
                    except JumpError as exc:
                        raise JumpError(f"Step {step['name']} ({step['target']}): {exc}") from exc
                    results[step["name"]] = result["output"]
                    if 'native_build' in result:
                        native_steps[step['name']] = result['native_build']
                output = self._bind(manifest["output"], inputs, results)
            validate(output, manifest["output_schema"], "output")
            elapsed = round((time.monotonic() - started) * 1000, 3)
            with self.catalog.db:
                self.catalog.db.execute("UPDATE runs SET duration_ms=?,success=1 WHERE id=?", (elapsed, run_id))
            result = {"id": target, "run_id": run_id, "duration_ms": elapsed, "output": output}
            if manifest.get('runtime') == 'cpp':
                result['native_build'] = self._native_status
            if native_steps:
                # Preserve backend evidence so callers can distinguish native execution from fallback.
                result['native_steps'] = native_steps
            return result
        except BaseException as exc:
            with self.catalog.db:
                # Persist error category only; arguments, results, and stderr can contain private data.
                self.catalog.db.execute("UPDATE runs SET duration_ms=?,success=0,error=? WHERE id=?",
                                        ((time.monotonic() - started) * 1000, type(exc).__name__, run_id))
            raise

    @staticmethod
    def _condition(step, inputs, results):
        """Return whether a step's bound value matches its optional JSON condition."""
        if "when" not in step:
            return True
        when = step["when"]
        return canonical(Runner._bind(when["value"], inputs, results)) == canonical(when["equals"])

    @staticmethod
    def _bind(binding, inputs, results):
        """Resolve a literal, input, or prior result; propagate invalid-pointer errors."""
        if "value" in binding:
            return binding["value"]
        if "input" in binding:
            return pointer(inputs, binding["input"])
        return pointer(results[binding["step"]], binding["pointer"])

    @staticmethod
    def _stop(process):
        """Kill and reap a child; on POSIX also stop its process-group descendants."""
        if os.name == "posix":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                # The process group already exited; there is nothing left to kill.
                pass
        elif process.poll() is None:
            process.kill()
        process.wait()

    def _program(self, row, manifest, inputs):
        """Verify an artifact, execute it with limits, and return decoded JSON.

        Raise JumpError on integrity, timeout, output, or process failures and
        always reap the child. Execution is not an operating-system sandbox.
        """
        # Check the complete registered inventory and content digest before launch.
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
        # Python needs no build directory; native builds never modify immutable bundles.
        if manifest['runtime'] == 'python':
            command = [sys.executable, '-B', str(artifact / manifest['entrypoint']), *manifest.get('arguments', [])]
            return self._execute(command, manifest, inputs)
        command = self._command(artifact, manifest, row['digest'], hashes)
        return self._execute(command, manifest, inputs)

    def _command(self, artifact, manifest, digest, hashes):
        """Use a ready binary or queue a build and select the declared Python fallback."""
        from .native_cache import cached_binary, wait_for_build

        entrypoint = manifest['entrypoint']
        self._native_status = {'status': 'disabled'}
        if manifest['runtime'] == 'cpp':
            settings = self.catalog.execution_settings()
            if settings['preferred_runtime'] == 'cpp':
                status = cached_binary(self.catalog.home, artifact, manifest, digest, settings, hashes)
                if status['status'] == 'building' and getattr(self, '_verifying', False):
                    status = wait_for_build(self.catalog.home / 'binaries' / status['build_key'])
                self._native_status = {key: value for key, value in status.items()
                                       if key not in ('dependencies', 'sha256', 'binary')}
                if status['status'] == 'ready':
                    return [status['binary'], *manifest.get('arguments', [])]
                if status['status'] == 'failed' and (getattr(self, '_verifying', False) or not manifest.get('python_fallback')):
                    raise JumpError(f"Background C++ build failed: {status['error']}")
            entrypoint = manifest.get('python_fallback')
            if not entrypoint:
                raise JumpError('C++ build pending or disabled; no Python fallback. Continue other work and retry after the build completes.')
        return [sys.executable, '-B', str(artifact / entrypoint), *manifest.get('arguments', [])]

    def _execute(self, command, manifest, inputs):
        """Run a selected command with bounded JSON I/O and timeout; always reap it."""
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
        """Explicit verification waits for native builds so fallback cannot validate C++."""
        self._verifying = True
        try:
            return self._verify(target, cases_file)
        finally:
            self._verifying = False

    def _verify(self, target, cases_file):
        """Run JSON fixtures and validate a target only after every case succeeds.

        Existing validation is revoked before execution. Fixture errors and
        mismatches raise JumpError; program side effects are not rolled back.
        """
        cases = parse_json(Path(cases_file).read_text())
        if not isinstance(cases, list) or not cases:
            raise JumpError("Verification needs a nonempty array of {input, output} cases")
        for case in cases:
            if not isinstance(case, dict) or not {"input", "output"} <= set(case) or set(case) - {"input", "output", "decisions"}:
                raise JumpError("Each verification case requires input/output and optional decisions")
            if not isinstance(case.get("decisions", {}), dict):
                raise JumpError("Verification decisions must map step names to values")
        self.catalog.get(target)
        self.catalog.set_validated(target, False)
        manifest = parse_json(self.catalog.get(target)["manifest"])
        resumable = manifest["kind"] == "workflow" and any("decision" in step for step in manifest["steps"])
        for index, case in enumerate(cases):
            if resumable:
                from .workflow import WorkflowSessions
                sessions = WorkflowSessions(self)
                receipt = sessions.start(target, case["input"], allow_experimental=True)
                decisions = dict(case.get("decisions", {}))
                while receipt["status"] == "needs_decision":
                    name = receipt["decision"]["step"]
                    if name not in decisions:
                        raise JumpError(f"Verification case {index + 1} lacks decision for {name}")
                    receipt = sessions.resume(receipt["session_id"], decisions.pop(name), step=name)
                if decisions:
                    raise JumpError("Verification case has unused decisions")
                output = sessions.result(receipt["session_id"])["output"]
            else:
                if case.get("decisions"):
                    raise JumpError("Verification case has unused decisions")
                output = self.run(target, case["input"], allow_experimental=True)["output"]
            if canonical(output) != canonical(case["output"]):
                raise JumpError(f"Verification case {index + 1} failed: output did not match expected JSON")
        self.catalog.set_validated(target, True)
        return {"id": target, "validated": True, "cases_passed": len(cases)}
