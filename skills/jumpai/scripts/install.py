"""Install JumpAI skill discovery together with persistent host instructions."""

from pathlib import Path
import sys

# Locate the checkout through the resolved skill link before importing its CLI.
repository = Path(__file__).resolve().parents[3]
if not (repository / 'jumpai/install.py').is_file():
    raise SystemExit('Run this installer from the JumpAI checkout or its installed skill link.')
sys.path.insert(0, str(repository))
from jumpai.install import main

raise SystemExit(main())
