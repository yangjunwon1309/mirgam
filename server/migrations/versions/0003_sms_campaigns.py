"""Add marketing consent records and scheduled SMS campaigns."""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('customers', sa.Column('sms_marketing_consent', sa.Boolean(), nullable=True))
    op.add_column('customers', sa.Column('sms_marketing_consent_at', sa.Text(), nullable=True))
    op.add_column('customers', sa.Column('sms_marketing_consent_source', sa.Text(), nullable=True))
    op.create_table(
        'sms_campaigns',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('accounts.id'), nullable=False),
        sa.Column('connection_id', sa.Integer(), sa.ForeignKey('sms_gate_connections.id'), nullable=False),
        sa.Column('body_ciphertext', sa.Text(), nullable=False),
        sa.Column('total_recipients', sa.Integer(), nullable=False),
        sa.Column('duration_days', sa.Integer(), nullable=False),
        sa.Column('daily_limit', sa.Integer(), nullable=False),
        sa.Column('starts_at', sa.Text(), nullable=False),
        sa.Column('ends_at', sa.Text(), nullable=False),
        sa.Column('state', sa.String(20), nullable=False),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.Text(), nullable=False),
    )
    op.create_index('ix_sms_campaigns_account_id', 'sms_campaigns', ['account_id'])
    op.create_index('ix_sms_campaigns_connection_id', 'sms_campaigns', ['connection_id'])
    op.create_table(
        'sms_campaign_messages',
        sa.Column('id', sa.String(64), primary_key=True),
        sa.Column('campaign_id', sa.String(64), sa.ForeignKey('sms_campaigns.id'), nullable=False),
        sa.Column('account_id', sa.Integer(), sa.ForeignKey('accounts.id'), nullable=False),
        sa.Column('connection_id', sa.Integer(), sa.ForeignKey('sms_gate_connections.id'), nullable=False),
        sa.Column('customer_id', sa.Integer(), sa.ForeignKey('customers.id'), nullable=False),
        sa.Column('recipient_phone', sa.Text(), nullable=False),
        sa.Column('body_ciphertext', sa.Text(), nullable=False),
        sa.Column('scheduled_at', sa.Text(), nullable=False),
        sa.Column('state', sa.String(20), nullable=False),
        sa.Column('attempt_count', sa.Integer(), nullable=False),
        sa.Column('provider_message_id', sa.String(160), unique=True),
        sa.Column('last_error_code', sa.String(100)),
        sa.Column('created_at', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.Text(), nullable=False),
    )
    for column in ('campaign_id', 'account_id', 'connection_id', 'scheduled_at'):
        op.create_index('ix_sms_campaign_messages_' + column, 'sms_campaign_messages', [column])


def downgrade():
    op.drop_table('sms_campaign_messages')
    op.drop_table('sms_campaigns')
    op.drop_column('customers', 'sms_marketing_consent_source')
    op.drop_column('customers', 'sms_marketing_consent_at')
    op.drop_column('customers', 'sms_marketing_consent')
