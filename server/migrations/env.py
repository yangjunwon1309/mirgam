from alembic import context
from alembic.ddl.sqlite import SQLiteImpl


class SQLiteCloudImpl(SQLiteImpl):
    """SQLite DDL behavior for the SQLite Cloud SQLAlchemy dialect."""
    __dialect__ = 'sqlitecloud'

config = context.config
with config.attributes['engine'].connect() as connection:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
