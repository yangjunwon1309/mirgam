"""Persistent schema; legacy CSV business values intentionally remain text."""
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def now():
    return datetime.now(timezone.utc).isoformat()


class Account(Base):
    __tablename__ = 'accounts'
    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False, unique=True)
    password_hash = Column(Text, nullable=False)
    customer_revision = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(Text, nullable=False, default=now)


class Customer(Base):
    __tablename__ = 'customers'
    __table_args__ = (UniqueConstraint('account_id', 'name', 'ph', 'address'),)
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('accounts.id'), nullable=False, index=True)
    name = Column(Text, nullable=False)
    ph = Column(Text, nullable=False)
    address = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(Text, nullable=False, default=now)
    updated_at = Column(Text, nullable=False, default=now)


class Product(Base):
    __tablename__ = 'products'
    id = Column(Integer, primary_key=True)
    item = Column(Text, nullable=False, unique=True)
    price = Column(Text, nullable=False, default='')
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(Text, nullable=False, default=now)
    updated_at = Column(Text, nullable=False, default=now)


class OrderBatch(Base):
    __tablename__ = 'order_batches'
    __table_args__ = (UniqueConstraint('account_id', 'request_id'),)
    id = Column(String(64), primary_key=True)
    account_id = Column(Integer, ForeignKey('accounts.id'), nullable=False)
    request_id = Column(String(128), nullable=False)
    payload_hash = Column(String(64), nullable=False)
    created_at = Column(Text, nullable=False, default=now)


class Order(Base):
    __tablename__ = 'orders'
    __table_args__ = (Index('ix_orders_account_day', 'account_id', 'order_day'),)
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('accounts.id'), nullable=False, index=True)
    customer_id = Column(Integer, ForeignKey('customers.id'), index=True)
    product_id = Column(Integer, ForeignKey('products.id'))
    batch_id = Column(String(64), ForeignKey('order_batches.id'), index=True)
    name = Column(Text, nullable=False)
    ph = Column(Text, nullable=False)
    address = Column(Text, nullable=False)
    item = Column(Text, nullable=False)
    quantity = Column(Text, nullable=False)
    date = Column(Text, nullable=False)
    sender_name = Column(Text, nullable=False, default='')
    sender_ph = Column(Text, nullable=False, default='')
    sender_address = Column(Text, nullable=False, default='')
    order_day = Column(String(10))
    is_deleted = Column(Boolean, nullable=False, default=False)
    created_at = Column(Text, nullable=False, default=now)


class AuthSession(Base):
    __tablename__ = 'auth_sessions'
    token_hash = Column(String(64), primary_key=True)
    account_id = Column(Integer, ForeignKey('accounts.id'), nullable=False)
    expires_at = Column(Text, nullable=False)


class CustomerImport(Base):
    __tablename__ = 'customer_import_jobs'
    id = Column(String(64), primary_key=True)
    account_id = Column(Integer, ForeignKey('accounts.id'), nullable=False)
    filename = Column(Text, nullable=False)
    file_hash = Column(String(64), nullable=False)
    source_path = Column(Text, nullable=False)
    expires_at = Column(Text, nullable=False)
    status = Column(String(20), nullable=False, default='uploaded')
    preview_version = Column(String(64))
    revision = Column(Integer)
    preview_json = Column(Text)
    result_json = Column(Text)


class MigrationRun(Base):
    __tablename__ = 'migration_runs'
    id = Column(Integer, primary_key=True)
    source_path = Column(Text, nullable=False, unique=True)
    manifest_hash = Column(String(64), nullable=False)
    report_json = Column(Text, nullable=False)
    created_at = Column(Text, nullable=False, default=now)


class SmsGateConnection(Base):
    __tablename__ = 'sms_gate_connections'
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('accounts.id'), nullable=False, unique=True)
    base_url = Column(Text, nullable=False)
    username = Column(Text, nullable=False)
    password_ciphertext = Column(Text, nullable=False)
    signing_key_ciphertext = Column(Text, nullable=False)
    device_id = Column(String(128), nullable=False, unique=True)
    phone_number = Column(Text, nullable=False)
    sim_number = Column(Integer, nullable=False, default=1)
    webhook_token = Column(String(64), nullable=False, unique=True)
    webhook_ids_json = Column(Text, nullable=False, default='{}')
    auto_ack_enabled = Column(Boolean, nullable=False, default=False)
    auto_ack_text = Column(Text, nullable=False, default='미르감: 주문이 접수되었습니다.')
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(Text, nullable=False, default=now)
    updated_at = Column(Text, nullable=False, default=now)


class SmsWebhookEvent(Base):
    __tablename__ = 'sms_webhook_events'
    __table_args__ = (UniqueConstraint('connection_id', 'provider_event_id'),)
    id = Column(Integer, primary_key=True)
    connection_id = Column(Integer, ForeignKey('sms_gate_connections.id'), nullable=False, index=True)
    provider_event_id = Column(String(160), nullable=False)
    event_type = Column(String(40), nullable=False)
    received_at = Column(Text, nullable=False, default=now)


class SmsInbound(Base):
    __tablename__ = 'sms_inbound'
    __table_args__ = (UniqueConstraint('connection_id', 'provider_event_id'),)
    id = Column(Integer, primary_key=True)
    account_id = Column(Integer, ForeignKey('accounts.id'), nullable=False, index=True)
    connection_id = Column(Integer, ForeignKey('sms_gate_connections.id'), nullable=False, index=True)
    provider_event_id = Column(String(160), nullable=False)
    provider_message_id = Column(String(160), nullable=False, default='')
    sender_phone = Column(Text, nullable=False, default='')
    received_at = Column(Text, nullable=False)
    body_ciphertext = Column(Text, nullable=False)
    body_hash = Column(String(64), nullable=False)
    suggested_customer_id = Column(Integer, ForeignKey('customers.id'))
    suggested_product_id = Column(Integer, ForeignKey('products.id'))
    suggested_quantity = Column(Text, nullable=False, default='')
    order_day = Column(String(10), nullable=False)
    state = Column(String(20), nullable=False, default='review')
    order_batch_id = Column(String(64), ForeignKey('order_batches.id'), unique=True)
    created_at = Column(Text, nullable=False, default=now)
    reviewed_at = Column(Text)


class SmsOutbox(Base):
    __tablename__ = 'sms_outbox'
    id = Column(String(64), primary_key=True)
    account_id = Column(Integer, ForeignKey('accounts.id'), nullable=False, index=True)
    connection_id = Column(Integer, ForeignKey('sms_gate_connections.id'), nullable=False, index=True)
    inbound_id = Column(Integer, ForeignKey('sms_inbound.id'), nullable=False, unique=True)
    order_batch_id = Column(String(64), ForeignKey('order_batches.id'), nullable=False)
    recipient_phone = Column(Text, nullable=False)
    body_ciphertext = Column(Text, nullable=False)
    state = Column(String(20), nullable=False, default='pending')
    attempt_count = Column(Integer, nullable=False, default=0)
    provider_message_id = Column(String(160), unique=True)
    last_error_code = Column(String(100))
    created_at = Column(Text, nullable=False, default=now)
    updated_at = Column(Text, nullable=False, default=now)
