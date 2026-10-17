"""Run the companion JumpAI checkout without depending on the caller's cwd."""

from pathlib import Path
import sys

# Locate the checkout through the resolved skill link before importing its CLI.
repository = Path(__file__).resolve().parents[3]
if not (repository / 'jumpai/cli.py').is_file():
    raise SystemExit('Keep this skill linked to its JumpAI checkout, or install the JumpAI package and use its jumpai CLI.')
sys.path.insert(0, str(repository))
from jumpai.cli import main

raise SystemExit(main())
