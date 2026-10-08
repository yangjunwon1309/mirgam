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
