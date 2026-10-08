"""Dry-run by default. --apply imports an explicitly selected CSV directory."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.db import Database
from server.migrate import inspect_source, migrate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--data-dir', default='var/server')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    database = None
    try:
        if args.apply:
            database = Database(args.data_dir)
            report = migrate(database, args.source, apply=True)
        else:
            report = dict(inspect_source(args.source)[-1], status='dry-run')
        print(json.dumps(report, ensure_ascii=True, indent=2))
        return 0
    except (ValueError, OSError) as error:
        print(str(error), file=sys.stderr)
        return 1
    finally:
        if database:
            database.engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
