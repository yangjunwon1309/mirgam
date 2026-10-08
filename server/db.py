"""Explicit SQLite transactions, schema versioning and online backups."""
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

SCHEMA_VERSION = '0002'
SUPPORTED_SCHEMA_VERSIONS = {'0001', '0002'}


class Database:
    def __init__(self, directory, initialize=True, database_url=None):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'mirgam.sqlite3'
        self.remote = bool(database_url)
        if self.remote:
            if not database_url.startswith('sqlitecloud://'):
                raise ValueError('MIRGAM_DATABASE_URL must use the sqlitecloud:// connection scheme')
            try:
                self.engine = create_engine(database_url, pool_pre_ping=True, pool_recycle=300)
            except Exception as error:
                if 'sqlitecloud' in str(error).lower() or 'dialect' in str(error).lower():
                    raise RuntimeError('Install requirements-server.txt to enable SQLite Cloud support') from error
                raise
        else:
            self.engine = create_engine('sqlite:///' + self.path.as_posix(),
                                        connect_args={'timeout': 5, 'check_same_thread': False})

            @event.listens_for(self.engine, 'connect')
            def configure(connection, _):
                connection.isolation_level = None
                connection.execute('PRAGMA foreign_keys=ON')
                connection.execute('PRAGMA busy_timeout=5000')

        if initialize and not self.remote:
            cfg = Config()
            cfg.set_main_option('script_location', str(Path(__file__).parent / 'migrations'))
            cfg.attributes['engine'] = self.engine
            command.upgrade(cfg, 'head')
        with self.engine.connect() as connection:
            version = connection.exec_driver_sql('SELECT version_num FROM alembic_version').scalar()
            if version not in SUPPORTED_SCHEMA_VERSIONS:
                raise RuntimeError('Unsupported database schema version')
            self.schema_version = version
            if not self.remote:
                connection.exec_driver_sql('PRAGMA journal_mode=WAL')

    @contextmanager
    def session(self, write=False):
        with self.engine.connect() as connection:
            # BEGIN IMMEDIATE serializes local SQLite writers before their first
            # SELECT. SQLite Cloud controls its own remote write concurrency.
            connection.exec_driver_sql('BEGIN' if self.remote or not write else 'BEGIN IMMEDIATE')
            with Session(bind=connection, expire_on_commit=False) as session:
                try:
                    yield session
                    session.flush()
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise

    def backup(self, reason='daily'):
        if self.remote:
            # Cloud-provider backups are managed separately; never create a
            # misleading local snapshot of a remote database.
            return None
        directory = self.directory.parent / (self.directory.name + '-backups')
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
        destination = directory / f'{stamp}_{reason}.sqlite3'
        with sqlite3.connect(self.path) as source, sqlite3.connect(destination) as target:
            source.backup(target)
            if target.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise RuntimeError('Backup integrity check failed')
        # Keep the latest 30 daily backups; pre-change snapshots are retained.
        if reason == 'daily':
            for old in sorted(directory.glob('*_daily.sqlite3'), reverse=True)[30:]:
                old.unlink()
        return destination
