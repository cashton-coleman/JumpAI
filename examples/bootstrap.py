"""Register and verify the bundled programs in an explicitly chosen catalog."""

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from jumpai.catalog import Catalog
from jumpai.runner import Runner
from jumpai.schema import canonical


def main():
    """Register and verify examples in the requested catalog; always close the connection."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', required=True)
    args = parser.parse_args()
    catalog = Catalog(args.home)
    try:
        runner = Runner(catalog, ROOT)
        for directory in sorted((ROOT / 'examples/programs').iterdir()):
            result = catalog.register(directory / 'manifest.json')
            print(canonical(result))
            print(canonical(runner.verify(result['id'], directory / 'cases.json')))
        result = catalog.register(ROOT / 'examples/workflows/summarize-json.json')
        print(canonical(result))
        print(canonical(runner.verify(result['id'], ROOT / 'examples/workflows/cases.json')))
    finally:
        catalog.close()


if __name__ == '__main__':
    main()
