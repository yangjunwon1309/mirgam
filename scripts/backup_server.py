import argparse
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.db import Database

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Create an online SQLite backup.')
    parser.add_argument('--data-dir', default='var/server')
    args = parser.parse_args()
    database = Database(args.data_dir, initialize=False)
    try:
        print(database.backup('manual'))
    finally:
        database.engine.dispose()
