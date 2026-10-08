"""Apply the reviewed 0002 SMS schema migration to the configured central DB."""
import argparse
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--yes', action='store_true', help='Confirm the migration after taking a cloud DB backup.')
    args = parser.parse_args()
    url = os.environ.get('MIRGAM_DATABASE_URL', '').strip()
    if not url.startswith('sqlitecloud://'):
        parser.error('MIRGAM_DATABASE_URL must be set to the central SQLite Cloud connection string.')
    engine = create_engine(url, pool_pre_ping=True)
    with engine.connect() as connection:
        version = connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
    if version == '0002':
        print('Schema is already at 0002; no changes made.')
        return
    if version != '0001':
        parser.error(f'Expected schema 0001, found {version!r}; refusing to migrate.')
    if not args.yes:
        parser.error('Back up/export the cloud database first, then rerun with --yes.')
    config = Config()
    config.set_main_option('script_location', str(ROOT / 'server' / 'migrations'))
    config.attributes['engine'] = engine
    command.upgrade(config, 'head')
    with engine.connect() as connection:
        final_version = connection.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
    if final_version != '0002':
        raise SystemExit(f'Migration finished at unexpected schema {final_version!r}.')
    print('Central SQLite schema migrated: 0001 -> 0002. Existing data was not rewritten.')


if __name__ == '__main__':
    main()
