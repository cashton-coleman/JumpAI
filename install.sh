#!/bin/sh
# Install or manage Jump AI from a complete checkout or extracted release package.
set -eu

# Resolve this package independently of the caller's working directory.
jumpai_root=$(CDPATH= cd "$(dirname "$0")" && pwd -P)

# Accept an explicit interpreter, or locate Python with SQLite FTS5 support.
supports_jumpai() {
    # Probe only prerequisites; return nonzero without modifying persistent state.
    "$1" -c 'import sys, sqlite3; assert sys.version_info >= (3, 11); sqlite3.connect(":memory:").execute("CREATE VIRTUAL TABLE probe USING fts5(value)")' >/dev/null 2>&1
}

jumpai_python=${JUMPAI_PYTHON:-}
if [ -n "$jumpai_python" ]; then
    if ! supports_jumpai "$jumpai_python"; then
        echo "JUMPAI_PYTHON must name Python 3.11+ with SQLite FTS5 support." >&2
        exit 1
    fi
else
    for candidate in python3 python3.14 python3.13 python3.12 python3.11; do
        if command -v "$candidate" >/dev/null 2>&1 && supports_jumpai "$candidate"; then
            jumpai_python=$candidate
            break
        fi
    done
    if [ -z "$jumpai_python" ]; then
        echo "Install Python 3.11+ with SQLite FTS5, then rerun this command. A C++ compiler is optional." >&2
        exit 1
    fi
fi

# Default to installation; forward lifecycle actions and explicit destination options.
if [ "$#" -eq 0 ]; then
    set -- install
fi
exec "$jumpai_python" "$jumpai_root/skills/jumpai/scripts/install.py" "$@" --quiet
