"""Run one detached native build using the installed skill's checkout."""

from pathlib import Path
import sys

# Resolve the skill symlink so the worker imports the same installed runtime.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from jumpai.native_cache import build_worker

if __name__ == '__main__':
    build_worker(sys.argv[1])
