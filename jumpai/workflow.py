"""Opt-in persisted workflows. Resume only at explicit decision checkpoints.

A checkpoint is not an exactly-once transaction with external side effects. Failed
or interrupted active execution is never automatically retried.
"""

import time
import uuid

from .schema import JumpError, canonical, parse_json, validate

# Keep persisted state and model-visible receipts bounded independently.
MAX_INPUT = 2 * 1024 * 1024
MAX_STATE = 8 * 1024 * 1024
MAX_CONTEXT = 4096
MAX_SUMMARY = 2048


class WorkflowSessions:
    """Persist deterministic steps and explicit decisions in the runner's catalog."""

    def __init__(self, runner):
        """Share the runner and its open catalog without taking ownership of either."""
        self.runner = runner
        self.catalog = runner.catalog

    def _load(self, session_id):
        """Return a session row and state; reject unknown IDs or workspace mismatch."""
        row = self.catalog.db.execute('SELECT * FROM workflow_sessions WHERE id=?', (session_id,)).fetchone()
        if row is None:
            raise JumpError(f'Unknown workflow session: {session_id}')
        if row['cwd'] != str(self.runner.cwd):
            raise JumpError('Session belongs to a different workspace; use its original --cwd')
        return row, parse_json(row['state'])

    def _manifest(self, target, experimental):
        """Return an eligible workflow manifest or raise JumpError before execution."""
        row = self.catalog.get(target)
        if not experimental:
            self.catalog.available(target)
        manifest = parse_json(row['manifest'])
        if manifest['kind'] != 'workflow':
            raise JumpError('--resumable requires a workflow')
        return manifest

    @staticmethod
    def _encoded(state):
        """Encode state for storage; reject values larger than the session limit."""
        encoded = canonical(state)
        if len(encoded.encode()) > MAX_STATE:
            raise JumpError('Workflow state exceeds 8 MiB; keep large evidence in artifact files')
        return encoded

    def start(self, target, inputs, *, allow_experimental=False):
        """Persist a new run and execute until completion, failure, or a decision.

        Return a compact receipt. Execution errors propagate and mark the session
        failed; external side effects cannot be rolled back by SQLite.
        """
        manifest = self._manifest(target, allow_experimental)
        validate(inputs, manifest['input_schema'])
        if len(canonical(inputs).encode()) > MAX_INPUT:
            raise JumpError('Input exceeds 2 MiB; pass file paths for large data')
        session_id = uuid.uuid4().hex
        now = time.time()
        with self.catalog.db:
            run_id = self.catalog.db.execute('INSERT INTO runs(target_id,started) VALUES (?,?)', (target, now)).lastrowid
            state = {'inputs': inputs, 'results': {}, 'next': 0, 'skipped': [], 'run_id': run_id,
                     'experimental': allow_experimental, 'active_ms': 0, 'revision': self.catalog.revision}
            self.catalog.db.execute('INSERT INTO workflow_sessions VALUES (?,?,?,?,?,?,?)',
                                    (session_id, target, str(self.runner.cwd), 'running', self._encoded(state), now, now))
        return self._drive(session_id, target, manifest, state)

    def resume(self, session_id, decision, *, step):
        """Claim a paused checkpoint and continue with a schema-checked decision.

        Reject stale steps and concurrent claims before executing further work.
        Return the next receipt or propagate an execution failure.
        """
        # Claim atomically so two callers cannot execute the continuation twice.
        with self.catalog.db:
            self.catalog.db.execute('BEGIN IMMEDIATE')
            row, state = self._load(session_id)
            if row['status'] != 'needs_decision':
                raise JumpError(f"Session is {row['status']}; only a paused decision can be resumed")
            manifest = self._manifest(row['target_id'], state['experimental'])
            pending = manifest['steps'][state['next']]
            if pending['name'] != step:
                raise JumpError('Stale decision: --step does not match the current checkpoint')
            validate(decision, pending['decision']['schema'], 'decision')
            if len(canonical(decision).encode()) > MAX_INPUT:
                raise JumpError('Decision exceeds 2 MiB')
            state['results'][step] = decision
            state['next'] += 1
            state.pop('pending', None)
            self.catalog.db.execute("UPDATE workflow_sessions SET status='running',state=?,updated=? WHERE id=?",
                                    (self._encoded(state), time.time(), session_id))
        return self._drive(session_id, row['target_id'], manifest, state)

    def _save(self, session_id, state, status):
        """Atomically persist bounded session state and its parent run's status."""
        with self.catalog.db:
            self.catalog.db.execute('UPDATE workflow_sessions SET state=?,status=?,updated=? WHERE id=?',
                                    (self._encoded(state), status, time.time(), session_id))
            self.catalog.db.execute('UPDATE runs SET duration_ms=?,success=?,error=? WHERE id=?',
                                    (round(state['active_ms'], 3), 1 if status == 'completed' else (0 if status == 'failed' else None),
                                     state.get('error'), state['run_id']))

    def _drive(self, session_id, target, manifest, state):
        """Execute remaining steps, persisting progress and returning a receipt.

        Stop at explicit decisions. On error preserve the last saved state and
        mark failure without retrying potentially side-effecting steps.
        """
        started = time.monotonic()
        base_ms = state['active_ms']
        try:
            while state['next'] < len(manifest['steps']):
                current = manifest['steps'][state['next']]
                name = current['name']
                inputs, results = state['inputs'], state['results']
                if not self.runner._condition(current, inputs, results):
                    results[name] = self.runner._bind(current.get('otherwise', {'value': None}), inputs, results)
                    state['skipped'].append(name)
                elif 'decision' in current:
                    spec = current['decision']
                    context = {key: self.runner._bind(binding, inputs, results) for key, binding in spec['context'].items()}
                    if len(canonical(context).encode()) > MAX_CONTEXT:
                        raise JumpError('Decision context exceeds 4 KiB; return a compact summary and evidence file path')
                    state['pending'] = {'step': name, 'prompt': spec['prompt'], 'schema': spec['schema'], 'context': context}
                    state['active_ms'] = base_ms + (time.monotonic() - started) * 1000
                    self._save(session_id, state, 'needs_decision')
                    return self.status(session_id)
                else:
                    bound = {key: self.runner._bind(binding, inputs, results) for key, binding in current['bindings'].items()}
                    result = self.runner.run(current['target'], bound, allow_experimental=state['experimental'], parent_id=state['run_id'])
                    results[name] = result['output']
                state['next'] += 1
                state['active_ms'] = base_ms + (time.monotonic() - started) * 1000
                self._save(session_id, state, 'running')
            output = self.runner._bind(manifest['output'], state['inputs'], state['results'])
            validate(output, manifest['output_schema'], 'output')
            state['output'] = output
            summary = self.runner._bind(manifest['summary'], state['inputs'], state['results']) if 'summary' in manifest else {'message': 'Workflow completed'}
            state['summary'] = summary if len(canonical(summary).encode()) <= MAX_SUMMARY else {'message': 'Workflow completed; summary exceeds 2 KiB, use result for details'}
            state['active_ms'] = base_ms + (time.monotonic() - started) * 1000
            self._save(session_id, state, 'completed')
            return self.status(session_id)
        except BaseException as exc:
            # Keep the last successfully persisted state if the new state is too large.
            _, saved = self._load(session_id)
            saved['error'] = type(exc).__name__
            saved['active_ms'] = base_ms + (time.monotonic() - started) * 1000
            self._save(session_id, saved, 'failed')
            if isinstance(exc, JumpError):
                raise JumpError(f'Workflow session {session_id} failed: {exc}') from exc
            raise

    def status(self, session_id):
        """Return a compact receipt without executing steps or changing the session."""
        row, state = self._load(session_id)
        result = {'session_id': session_id, 'id': row['target_id'], 'status': row['status'],
                  'steps_completed': state['next'], 'steps_skipped': len(state['skipped']),
                  'active_ms': round(state['active_ms'], 3), 'experimental': state['experimental']}
        if row['status'] == 'needs_decision':
            result['decision'] = state['pending']
        elif row['status'] == 'completed':
            result.update(summary=state['summary'], result_ref=session_id)
        elif row['status'] == 'failed':
            result['error'] = state.get('error', 'Execution failed')
        return result

    def result(self, session_id, *, step=None):
        """Return a stored step or final output; reject missing or unfinished results."""
        row, state = self._load(session_id)
        if step is not None:
            if step not in state['results']:
                raise JumpError('No stored result for that step')
            return {'session_id': session_id, 'step': step, 'output': state['results'][step]}
        if row['status'] != 'completed':
            raise JumpError('Workflow has no completed output; use status or result --step')
        return {'session_id': session_id, 'output': state['output']}
