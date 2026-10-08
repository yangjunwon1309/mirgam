from alembic import context

config = context.config
with config.attributes['engine'].connect() as connection:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()
