"""Copy the local Mirgam SQLite database to SQLite Cloud through Weblite REST.

The source database stays local and unchanged. The script creates a NEW remote
database and sends schema/data directly over HTTPS; it does not expose a local
file URL or replace an existing remote database.
"""
import argparse
from getpass import getpass
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
from urllib.parse import quote

import requests


DEFAULT_SOURCE = Path(__file__).resolve().parents[1] / 'var' / 'server' / 'mirgam.sqlite3'
TABLE_ORDER = (
    'alembic_version', 'accounts', 'products', 'customers', 'order_batches',
    'orders', 'auth_sessions', 'customer_import_jobs', 'migration_runs',
)
BATCH_SIZE = 50
TRANSIENT_TABLES = {'auth_sessions', 'customer_import_jobs'}


class TransferError(RuntimeError):
    pass


def qident(value):
    return '"' + value.replace('"', '""') + '"'


def read_source(path):
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise TransferError(f'Source database does not exist: {path}')

    snapshot_directory = tempfile.TemporaryDirectory(prefix='mirgam-sqlitecloud-')
    snapshot_path = Path(snapshot_directory.name) / 'mirgam.sqlite3'
    try:
        # SQLite's backup API creates a consistent snapshot even when the local
        # API is running in WAL mode; the original file is never modified.
        with sqlite3.connect(path) as live, sqlite3.connect(snapshot_path) as snapshot:
            live.backup(snapshot)
    except BaseException:
        snapshot_directory.cleanup()
        raise

    connection = sqlite3.connect(f'file:{snapshot_path.as_posix()}?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        integrity = connection.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity != 'ok':
            raise TransferError(f'Source database integrity check failed: {integrity}')

        objects = connection.execute(
            "SELECT type, name, sql FROM sqlite_master "
            "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()
        tables = {row['name']: row['sql'] for row in objects if row['type'] == 'table'}
        missing = set(TABLE_ORDER) - set(tables)
        if missing:
            raise TransferError('Not a recognized Mirgam database; missing tables: ' + ', '.join(sorted(missing)))
        version = connection.execute('SELECT version_num FROM alembic_version').fetchone()[0]
        if version != '0001':
            raise TransferError(f'Unsupported Mirgam schema version: {version}')

        ordered_tables = [name for name in TABLE_ORDER if name in tables]
        ordered_tables.extend(sorted(set(tables) - set(ordered_tables)))
        counts = {
            name: connection.execute(f'SELECT COUNT(*) FROM {qident(name)}').fetchone()[0]
            for name in ordered_tables
        }
        indexes = [row['sql'] for row in objects if row['type'] == 'index' and row['sql']]
        return connection, path, ordered_tables, tables, indexes, counts, snapshot_directory
    except BaseException:
        connection.close()
        snapshot_directory.cleanup()
        raise


class Weblite:
    def __init__(self, base_url, api_key):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self.session.headers.update({
            'Authorization': 'Bearer ' + api_key,
            'Accept': 'application/json',
        })

    def request(self, method, path, payload=None):
        try:
            response = self.session.request(
                method, self.base_url + path, json=payload, timeout=(10, 120)
            )
        except requests.RequestException as error:
            raise TransferError(f'Could not reach SQLite Cloud Weblite: {error}') from error
        try:
            result = response.json()
        except ValueError:
            result = None
        if not response.ok:
            message = result if isinstance(result, dict) else response.text[:500]
            raise TransferError(f'Weblite returned HTTP {response.status_code}: {message}')
        if isinstance(result, dict) and (result.get('error') or result.get('errors')):
            raise TransferError(f'Weblite error: {result.get("error") or result.get("errors")}')
        return result

    def databases(self):
        result = self.request('GET', '/v2/weblite/databases')
        if not isinstance(result, dict) or not isinstance(result.get('data'), list):
            raise TransferError('Unexpected response while listing SQLite Cloud databases')
        return result['data']

    def execute_sql(self, database, sql):
        return self.request('POST', '/v2/weblite/sql', {
            'sql': sql,
            'database': database,
        })

    def insert_rows(self, database, table, rows):
        return self.request(
            'POST', f'/v2/weblite/{quote(database, safe="")}/{quote(table, safe="")}', rows
        )


def json_row(row):
    converted = {}
    for key, value in row.items():
        if isinstance(value, bytes):
            raise TransferError(f'BLOB column is not supported by this uploader: {key}')
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise TransferError(f'Unsupported value in column {key}: {type(value).__name__}')
        converted[key] = value
    return converted


def transfer(source, ordered_tables, table_sql, indexes, expected_counts, remote, target, bootstrap):
    existing = remote.databases()
    names = {
        row.get('name') for row in existing
        if isinstance(row, dict) and isinstance(row.get('name'), str)
    }
    if target in names:
        raise TransferError(
            f'Remote database already exists: {target}. Choose a new name; this tool never replaces databases.'
        )
    if bootstrap not in names:
        raise TransferError(
            f'Bootstrap database {bootstrap!r} was not found. Set --bootstrap-database to an existing project database.'
        )

    destination_may_be_partial = False
    try:
        # CREATE DATABASE is issued in the context of an existing database. It
        # does not modify that database; it creates a separate destination.
        print(f'Creating new cloud database: {target}', flush=True)
        destination_may_be_partial = True
        remote.execute_sql(bootstrap, f'CREATE DATABASE {target}')
        print('Cloud database created; transferring schema and rows.', flush=True)

        for table in ordered_tables:
            remote.execute_sql(target, table_sql[table])

        for table in ordered_tables:
            if table in TRANSIENT_TABLES:
                print(f'Skipped transient table data: {table}', flush=True)
                continue
            columns = [row[1] for row in source.execute(f'PRAGMA table_info({qident(table)})')]
            if not columns:
                raise TransferError(f'Could not read columns for table {table}')
            cursor = source.execute(f'SELECT * FROM {qident(table)}')
            while True:
                batch = cursor.fetchmany(BATCH_SIZE)
                if not batch:
                    break
                rows = [json_row(dict(row)) for row in batch]
                remote.insert_rows(target, table, rows)
            print(f'Transferred {table}: {expected_counts[table]} rows', flush=True)

        # Recreate named indexes after inserting the data. Autoindexes created
        # by UNIQUE/PRIMARY KEY constraints are part of the table DDL.
        for statement in indexes:
            remote.execute_sql(target, statement)

        for table in ordered_tables:
            result = remote.execute_sql(
                target, f'SELECT COUNT(*) AS row_count FROM {qident(table)}'
            )
            rows = result.get('data') if isinstance(result, dict) else None
            if not isinstance(rows, list) or len(rows) != 1:
                raise TransferError(f'Could not verify row count for remote table {table}')
            actual = rows[0].get('row_count') if isinstance(rows[0], dict) else None
            if actual is None:
                actual = next(iter(rows[0].values())) if isinstance(rows[0], dict) and rows[0] else None
            if int(actual) != expected_counts[table]:
                raise TransferError(
                    f'Row-count mismatch for {table}: local={expected_counts[table]}, remote={actual}'
                )
    except TransferError as error:
        error.destination_may_be_partial = destination_may_be_partial
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default=str(DEFAULT_SOURCE), help='Local Mirgam SQLite file')
    parser.add_argument('--database', required=True, help='New SQLite Cloud database name (must not already exist)')
    parser.add_argument('--bootstrap-database', default='chinook.sqlite',
                        help='Existing database used to issue CREATE DATABASE')
    parser.add_argument('--dry-run', action='store_true', help='Validate source and print table counts only')
    parser.add_argument('--yes', action='store_true', help='Confirm sending customer/order data to SQLite Cloud')
    args = parser.parse_args(argv)

    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', args.database):
        parser.error('--database may contain only letters, digits, dot, underscore, or hyphen (max 64 chars)')
    if args.database == args.bootstrap_database:
        parser.error('Destination database must differ from the bootstrap database')

    source, path, tables, table_sql, indexes, counts, snapshot_directory = read_source(args.source)
    try:
        print(f'Source: {path}')
        print('SQLite integrity: ok; schema version: 0001')
        for table in tables:
            print(f'  {table}: {counts[table]} rows')
        if args.dry_run:
            print('  Note: auth_sessions and customer_import_jobs are schema-only in the cloud copy;')
            print('        everyone will log in again and pending import previews must be restarted.')
            return 0

        base_url = os.environ.get('SQLITECLOUD_WEBLITE_BASE_URL', '').strip()
        api_key = os.environ.get('SQLITECLOUD_API_KEY', '').strip()
        try:
            if not base_url:
                base_url = input('SQLite Cloud Weblite base URL (paste the latest dashboard URL): ').strip()
            if not api_key:
                api_key = getpass('SQLite Cloud API key (input hidden): ').strip()
        except (EOFError, KeyboardInterrupt) as error:
            raise TransferError('Credential entry cancelled; no remote request was made.') from error
        if not base_url or not api_key:
            raise TransferError('Both the Weblite base URL and API key are required; no remote request was made.')
        if not base_url.startswith('https://'):
            raise TransferError('Weblite base URL must start with https://')
        if not args.yes:
            raise TransferError('No remote changes made. Re-run with --yes after reviewing the table counts.')

        remote = Weblite(base_url, api_key)
        transfer_counts = {name: (0 if name in TRANSIENT_TABLES else count)
                           for name, count in counts.items()}
        transfer(source, tables, table_sql, indexes, transfer_counts, remote,
                 args.database, args.bootstrap_database)
        print(f'Upload and row-count verification completed: {args.database}')
        print('The local SQLite database was not modified.')
        print('Transient login sessions and customer-import previews were not transferred.')
        return 0
    except TransferError as error:
        print(f'ERROR: {error}', file=sys.stderr)
        if getattr(error, 'destination_may_be_partial', False):
            print(f'The destination may be partial; inspect it before retrying: {args.database}', file=sys.stderr)
        return 1
    finally:
        source.close()
        snapshot_directory.cleanup()


if __name__ == '__main__':
    raise SystemExit(main())
