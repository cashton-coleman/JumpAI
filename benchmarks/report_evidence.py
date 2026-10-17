"""Render the audited evidence comparison using the reusable report implementation."""

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT=ROOT/'benchmarks/agent-results/2026-10-16-evidence'

def main():
    """Render the prepared evidence comparison using the cataloged report program."""
    config=json.loads((OUT/'evaluation-input.json').read_text())
    args=dict(config['report'],output_dir=config['audit']['output_dir'],conditions=config['audit']['conditions'])
    spec=importlib.util.spec_from_file_location('benchmark_report',ROOT/'programs/benchmark-report/render.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.render(args)
    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main()
