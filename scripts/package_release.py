"""Build a complete source release and checksum, excluding local state and reports."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import tarfile
import tempfile
import tomllib


# Include runtime dependencies and source fixtures, never personal catalogs or telemetry.
ROOT = Path(__file__).resolve().parents[1]
TOP_FILES = ('README.md', 'FAQ.md', 'HOW_IT_WORKS.md', 'DEVELOPER_GUIDE.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'pyproject.toml',
             'install.sh', 'install-from-github.sh', '.gitignore', 'AGENTS.md', 'AGENTS.override.md', 'CLAUDE.md')
SOURCE_DIRS = ('jumpai', 'skills', 'programs', 'workflows', 'examples', 'benchmarks', 'tests', 'scripts')
EXCLUDED = {'__pycache__', '.jumpai', '.git', '.venv', 'results', 'agent-results'}
SOURCE_SUFFIXES = {'.py', '.cpp', '.hpp', '.json', '.md', '.yaml', '.yml', '.txt', '.toml', '.sh', '.MIT', '.png'}


def package(output):
    """Write a versioned tarball and SHA-256 file; fail on missing or escaping sources."""
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+(?:[-.][A-Za-z0-9]+)*', version):
        raise ValueError('Unsafe or unsupported release version')
    name = f'jumpai-{version}'
    files = {ROOT / item for item in TOP_FILES}
    for folder in SOURCE_DIRS:
        for path in (ROOT / folder).rglob('*'):
            if path.is_file() and path.suffix in SOURCE_SUFFIXES and not EXCLUDED.intersection(path.relative_to(ROOT).parts):
                files.add(path)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f'{name}.tar.gz'

    # Stage the archive before replacing an earlier local build of the same version.
    with tempfile.TemporaryDirectory(prefix='.release-', dir=output) as temp:
        staged = Path(temp) / archive.name
        with tarfile.open(staged, 'w:gz', dereference=True) as bundle:
            for path in sorted(files):
                if not path.is_file() or not path.resolve().is_relative_to(ROOT):
                    raise ValueError(f'Missing source or path outside checkout: {path}')
                info = bundle.gettarinfo(str(path), arcname=f'{name}/{path.relative_to(ROOT)}')
                info.uid = info.gid = 0
                info.uname = info.gname = ''
                info.mode = 0o755 if path.suffix == '.sh' else 0o644
                with path.open('rb') as stream:
                    bundle.addfile(info, stream)
        staged.replace(archive)

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    checksum = output / f'{archive.name}.sha256'
    checksum.write_text(f'{digest}  {archive.name}\n')
    return {'archive': str(archive), 'sha256': digest, 'checksum': str(checksum), 'files': len(files)}


def main():
    """Build into the requested output directory and print artifact locations as JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    print(json.dumps(package(args.output.resolve())))


if __name__ == '__main__':
    main()
