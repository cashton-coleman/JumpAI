"""Start the idle-expiring native dependency watcher for one writable catalog."""

from pathlib import Path
import sys

# Resolve installed symlinks to import the matching runtime checkout.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from jumpai.cache_guard import serve

if __name__ == '__main__':
    serve(sys.argv[1])
