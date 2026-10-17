"""Read local Codex turn counters without exporting conversation text or changing state."""

from datetime import datetime
import json
import os
from pathlib import Path
from uuid import UUID


# Cached input is a subset of input; reasoning is already included in output.
TOKEN_FIELDS = ('input_tokens', 'cached_input_tokens', 'output_tokens', 'total_tokens')
MAX_RECORD_BYTES = 8 * 1024 * 1024


def token_counts(value):
    """Return validated cumulative counters, or None for missing/inconsistent telemetry."""
    if not isinstance(value, dict):
        return None
    if any(type(value.get(key)) is not int or value[key] < 0 for key in TOKEN_FIELDS):
        return None
    if value['cached_input_tokens'] > value['input_tokens']:
        return None
    if value['total_tokens'] != value['input_tokens'] + value['output_tokens']:
        return None
    return {key: value[key] for key in TOKEN_FIELDS}


def execution_receipt(value, depth=0):
    """Recognize execution receipts inside tool wrappers, never arbitrary source fields."""
    if depth > 8:
        return False
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            return False
    if isinstance(value, list):
        return any(execution_receipt(item, depth + 1) for item in value)
    if not isinstance(value, dict):
        return False
    if value.get('jumpai_execution') is True:
        return True
    # Older program receipts have no explicit execution marker.
    if (type(value.get('run_id')) is int and isinstance(value.get('id'), str)
            and '@' in value['id'] and 'duration_ms' in value and 'output' in value):
        return True
    return any(execution_receipt(value[key], depth + 1)
               for key in ('output', 'text', 'content') if key in value)


def completed_turn(task, end, counters):
    """Calculate a completed turn's wall time and deltas; never infer missing counters."""
    seconds = (datetime.fromisoformat(end) - datetime.fromisoformat(task['start'])).total_seconds()
    if seconds < 0:
        raise ValueError('Turn completion precedes its start')
    usage = None
    if task['baseline'] is not None and counters is not None and task['has_usage']:
        usage = token_counts({key: counters[key] - task['baseline'][key] for key in TOKEN_FIELDS})
    return {'turn_id': task['id'], 'wall_seconds': round(seconds, 3),
            'jumpai_used': task['jumpai_used'], 'usage': usage}


def read_turns(path):
    """Stream complete JSONL records; ignore an unfinished tail and reject corrupt records."""
    counters = None
    task = None
    turns = []
    with path.open('rb') as stream:
        while line := stream.readline(MAX_RECORD_BYTES + 1):
            if len(line) > MAX_RECORD_BYTES:
                raise ValueError('Usage record exceeds the supported 8 MiB limit')
            if not line.endswith(b'\n'):
                break
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError('Expected a JSON object in the session log')
            payload = record.get('payload', {})
            if not isinstance(payload, dict):
                continue
            kind = payload.get('type')

            # Only returned tool data can establish that Jump AI actually executed.
            if record.get('type') == 'response_item':
                if task is not None and kind in {'function_call_output', 'custom_tool_call_output'}:
                    task['jumpai_used'] |= execution_receipt(payload.get('output'))
                continue
            if record.get('type') != 'event_msg':
                continue
            if kind == 'task_started':
                if not isinstance(payload.get('turn_id'), str) or not payload['turn_id']:
                    raise ValueError('Task start has no turn ID')
                task = {'id': payload.get('turn_id'), 'start': record['timestamp'],
                        'baseline': counters, 'has_usage': False, 'jumpai_used': False}
            elif kind == 'token_count':
                info = payload.get('info')
                if not isinstance(info, dict):
                    continue
                updated = token_counts(info.get('total_token_usage'))
                if task is not None:
                    if (updated is None or counters is None
                            or any(updated[key] < counters[key] for key in TOKEN_FIELDS)):
                        task['baseline'] = None
                    task['has_usage'] |= updated != counters
                counters = updated
            elif kind == 'task_complete' and task is not None:
                if payload.get('turn_id') == task['id']:
                    turns.append(completed_turn(task, record['timestamp'], counters))
                task = None
            elif kind == 'turn_aborted':
                task = None
    return turns


def session_path(session=None, thread_id=None, sessions_dir=None):
    """Locate only the selected Codex thread; require an explicit path if it is ambiguous."""
    if session:
        return Path(session).expanduser()
    identity = thread_id or os.environ.get('CODEX_THREAD_ID')
    if not identity:
        raise ValueError('No current thread ID; supply --session or --thread-id')
    identity = str(UUID(identity))
    profile = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex')))
    root = Path(sessions_dir).expanduser() if sessions_dir else profile / 'sessions'
    matches = list(root.glob(f'*/*/*/rollout-*-{identity}.jsonl'))
    if len(matches) != 1:
        raise ValueError('Expected one local session for this thread; supply --session')
    return matches[0]


def report(session=None, thread_id=None, sessions_dir=None):
    """Return previous-turn usage and same-thread measured Jump AI means, or unavailability."""
    try:
        turns = read_turns(session_path(session, thread_id, sessions_dir))
        assisted = [turn for turn in turns if turn['jumpai_used']]
        measured = [turn for turn in assisted if turn['usage'] is not None]
        average = None
        if measured:
            average = {'turns': len(measured),
                       'wall_seconds': round(sum(t['wall_seconds'] for t in measured) / len(measured), 3),
                       'total_tokens': round(sum(t['usage']['total_tokens'] for t in measured) / len(measured), 1)}
        return {'previous': turns[-1] if turns else None, 'jumpai_average': average,
                'jumpai_unmeasured_turns': len(assisted) - len(measured)}
    except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
        return {'previous': None, 'jumpai_average': None, 'unavailable': str(exc)}


def footer(result):
    """Format one brief line from recorded measurements, including explicit missing-data labels."""
    previous = result['previous']
    details = 'unavailable'
    if previous is not None:
        details = f"{previous['wall_seconds']:.1f}s"
        usage = previous['usage']
        if usage is None:
            details += ' · tokens unavailable'
        else:
            details += (f" · in {usage['input_tokens']:,} ({usage['cached_input_tokens']:,} cached)"
                        f" · out {usage['output_tokens']:,} · total {usage['total_tokens']:,}")
    average = result['jumpai_average']
    mean = 'unavailable'
    if average is not None:
        mean = f"n={average['turns']}: {average['wall_seconds']:.1f}s · {average['total_tokens']:,.0f} tokens"
    skipped = result.get('jumpai_unmeasured_turns', 0)
    if skipped:
        mean += f' ({skipped} unmeasured)'
    return f'Usage (previous): {details} | Jump AI avg: {mean}.'
