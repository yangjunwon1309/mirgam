"""Add SMS Gateway connections, received order drafts and outbox."""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'sms_gate_connections',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('accounts.id'), nullable=False, unique=True),
        sa.Column('base_url', sa.Text(), nullable=False),
        sa.Column('username', sa.Text(), nullable=False),
        sa.Column('password_ciphertext', sa.Text(), nullable=False),
        sa.Column('signing_key_ciphertext', sa.Text(), nullable=False),
        sa.Column('device_id', sa.String(128), nullable=False, unique=True),
        sa.Column('phone_number', sa.Text(), nullable=False),
        sa.Column('sim_number', sa.Integer(), nullable=False),
        sa.Column('webhook_token', sa.String(64), nullable=False, unique=True),
        sa.Column('webhook_ids_json', sa.Text(), nullable=False),
        sa.Column('auto_ack_enabled', sa.Boolean(), nullable=False),
        sa.Column('auto_ack_text', sa.Text(), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.Text(), nullable=False),
    )
    op.create_table(
        'sms_webhook_events',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('connection_id', sa.Integer(), sa.ForeignKey('sms_gate_connections.id'), nullable=False),
        sa.Column('provider_event_id', sa.String(160), nullable=False),
        sa.Column('event_type', sa.String(40), nullable=False),
        sa.Column('received_at', sa.Text(), nullable=False),
        sa.UniqueConstraint('connection_id', 'provider_event_id'),
    )
    op.create_index('ix_sms_webhook_events_connection_id', 'sms_webhook_events', ['connection_id'])
    op.create_table(
        'sms_inbound',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('accounts.id'), nullable=False),
        sa.Column('connection_id', sa.Integer(), sa.ForeignKey('sms_gate_connections.id'), nullable=False),
        sa.Column('provider_event_id', sa.String(160), nullable=False),
        sa.Column('provider_message_id', sa.String(160), nullable=False),
        sa.Column('sender_phone', sa.Text(), nullable=False),
        sa.Column('received_at', sa.Text(), nullable=False),
        sa.Column('body_ciphertext', sa.Text(), nullable=False),
        sa.Column('body_hash', sa.String(64), nullable=False),
        sa.Column('suggested_customer_id', sa.Integer(), sa.ForeignKey('customers.id')),
        sa.Column('suggested_product_id', sa.Integer(), sa.ForeignKey('products.id')),
        sa.Column('suggested_quantity', sa.Text(), nullable=False),
        sa.Column('order_day', sa.String(10), nullable=False),
        sa.Column('state', sa.String(20), nullable=False),
        sa.Column('order_batch_id', sa.String(64), sa.ForeignKey('order_batches.id'), unique=True),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.Column('reviewed_at', sa.Text()),
        sa.UniqueConstraint('connection_id', 'provider_event_id'),
    )
    op.create_index('ix_sms_inbound_account_id', 'sms_inbound', ['account_id'])
    op.create_index('ix_sms_inbound_connection_id', 'sms_inbound', ['connection_id'])
    op.create_table(
        'sms_outbox',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('accounts.id'), nullable=False),
        sa.Column('connection_id', sa.Integer(), sa.ForeignKey('sms_gate_connections.id'), nullable=False),
        sa.Column('inbound_id', sa.Integer(), sa.ForeignKey('sms_inbound.id'), nullable=False, unique=True),
        sa.Column('order_batch_id', sa.String(64), sa.ForeignKey('order_batches.id'), nullable=False),
        sa.Column('recipient_phone', sa.Text(), nullable=False),
        sa.Column('body_ciphertext', sa.Text(), nullable=False),
        sa.Column('state', sa.String(20), nullable=False),
        sa.Column('attempt_count', sa.Integer(), nullable=False),
        sa.Column('provider_message_id', sa.String(160), unique=True),
        sa.Column('last_error_code', sa.String(100)),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.Text(), nullable=False),
    )
    op.create_index('ix_sms_outbox_account_id', 'sms_outbox', ['account_id'])
    op.create_index('ix_sms_outbox_connection_id', 'sms_outbox', ['connection_id'])


def downgrade():
    op.drop_table('sms_outbox')
    op.drop_table('sms_inbound')
    op.drop_table('sms_webhook_events')
    op.drop_table('sms_gate_connections')
