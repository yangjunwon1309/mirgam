"""Initial CSV-compatible schema, frozen independently of application models."""
from alembic import op
import sqlalchemy as sa

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def ident():
    return sa.Column('id', sa.Integer(), primary_key=True)


def text(name, nullable=False):
    return sa.Column(name, sa.Text(), nullable=nullable)


def account():
    return sa.Column('account_id', sa.Integer(), sa.ForeignKey('accounts.id'), nullable=False)


def active():
    return sa.Column('is_active', sa.Boolean(), nullable=False)


def upgrade():
    op.create_table('accounts', ident(), text('name'), text('password_hash'),
                    sa.Column('customer_revision', sa.Integer(), nullable=False), active(),
                    text('created_at'), sa.UniqueConstraint('name'))
    op.create_table('customers', ident(), account(), text('name'), text('ph'), text('address'),
                    active(), text('created_at'), text('updated_at'),
                    sa.UniqueConstraint('account_id', 'name', 'ph', 'address'))
    op.create_index('ix_customers_account_id', 'customers', ['account_id'])
    op.create_table('products', ident(), text('item'), text('price'), active(),
                    text('created_at'), text('updated_at'), sa.UniqueConstraint('item'))
    op.create_table('order_batches', sa.Column('id', sa.String(64), primary_key=True), account(),
                    sa.Column('request_id', sa.String(128), nullable=False),
                    sa.Column('payload_hash', sa.String(64), nullable=False), text('created_at'),
                    sa.UniqueConstraint('account_id', 'request_id'))
    op.create_table('orders', ident(), account(),
                    sa.Column('customer_id', sa.Integer(), sa.ForeignKey('customers.id')),
                    sa.Column('product_id', sa.Integer(), sa.ForeignKey('products.id')),
                    sa.Column('batch_id', sa.String(64), sa.ForeignKey('order_batches.id')),
                    *[text(name) for name in ('name', 'ph', 'address', 'item', 'quantity', 'date',
                                              'sender_name', 'sender_ph', 'sender_address')],
                    sa.Column('order_day', sa.String(10)),
                    sa.Column('is_deleted', sa.Boolean(), nullable=False), text('created_at'))
    for column in ('account_id', 'customer_id', 'batch_id'):
        op.create_index('ix_orders_' + column, 'orders', [column])
    op.create_index('ix_orders_account_day', 'orders', ['account_id', 'order_day'])
    op.create_table('auth_sessions', sa.Column('token_hash', sa.String(64), primary_key=True),
                    account(), text('expires_at'))
    op.create_table('customer_import_jobs', sa.Column('id', sa.String(64), primary_key=True),
                    account(), text('filename'), sa.Column('file_hash', sa.String(64), nullable=False),
                    text('source_path'), text('expires_at'), sa.Column('status', sa.String(20), nullable=False),
                    sa.Column('preview_version', sa.String(64)), sa.Column('revision', sa.Integer()),
                    text('preview_json', True), text('result_json', True))
    op.create_table('migration_runs', ident(), text('source_path'),
                    sa.Column('manifest_hash', sa.String(64), nullable=False),
                    text('report_json'), text('created_at'), sa.UniqueConstraint('source_path'))


def downgrade():
    for table in ('migration_runs', 'customer_import_jobs', 'auth_sessions', 'orders',
                  'order_batches', 'products', 'customers', 'accounts'):
        op.drop_table(table)
