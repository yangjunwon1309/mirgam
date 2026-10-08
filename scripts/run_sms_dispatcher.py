"""Run the SMS outbox worker; keep this separate from the Flask web process."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from server.db import Database
from server.sms_dispatcher import run


def main():
    if not os.environ.get('MIRGAM_DATABASE_URL', '').startswith('sqlitecloud://'):
        raise SystemExit('Set MIRGAM_DATABASE_URL to the central SQLite Cloud connection string.')
    if not os.environ.get('MIRGAM_SMS_ENCRYPTION_KEY'):
        raise SystemExit('Set MIRGAM_SMS_ENCRYPTION_KEY to the same secret used by the API server.')
    database = Database(os.environ.get('MIRGAM_SERVER_DATA_DIR', 'var/server'), initialize=False)
    print('Mirgam SMS dispatcher is running. Press Ctrl+C to stop.')
    run(database)


if __name__ == '__main__':
    main()
