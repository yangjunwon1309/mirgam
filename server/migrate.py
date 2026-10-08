"""Explicit, read-only CSV source selection and repeat-safe initial migration."""
import csv
import io
import json
from pathlib import Path
import shutil

from sqlalchemy import select
from werkzeug.security import generate_password_hash

from .common import CUSTOMER_COLUMNS, ORDER_COLUMNS, digest, order_day, payload_hash
from .models import Account, Customer, MigrationRun, Order, Product


def read_csv(path, required, manifest=None):
    raw = path.read_bytes()
    if manifest is not None:
        manifest[path.name] = digest(raw)
    with io.StringIO(raw.decode('utf-8-sig'), newline='') as file:
        reader = csv.DictReader(file)
        if not set(required).issubset(reader.fieldnames or []):
            raise ValueError(f'Invalid columns: {path.name}')
        rows = list(reader)
        if any(None in row for row in rows):
            raise ValueError(f'Extra unnamed cells: {path.name}')
        return [{key: value or '' for key, value in row.items()} for row in rows]


def inspect_source(directory):
    source = Path(directory).resolve()
    manifest = {}
    accounts = read_csv(source / 'login.csv', ['name', 'pw'], manifest)
    if len({row['name'] for row in accounts}) != len(accounts):
        raise ValueError('Duplicate login accounts; resolve the source before migration')
    products = read_csv(source / 'items.csv', ['item', 'price'], manifest)
    price_map = {}
    for product in products:
        previous = price_map.setdefault(product['item'], product['price'])
        if previous != product['price']:
            raise ValueError('Conflicting prices for the same product')
    datasets, summaries = [], []
    for account in accounts:
        name = account['name']
        if not name or any(character in name for character in '/\\') or not account['pw']:
            raise ValueError('Invalid account name/password in login.csv')
        customer_file = source / f'customer_upload_{name}.csv'
        order_file = source / f'order_{name}.csv'
        # Never silently fall back to customer_<name>.csv.
        customers = read_csv(customer_file, CUSTOMER_COLUMNS, manifest)
        orders = read_csv(order_file, ORDER_COLUMNS[:6], manifest)
        legacy = source / f'customer_{name}.csv'
        different = False
        if legacy.exists():
            different = read_csv(legacy, CUSTOMER_COLUMNS, manifest) != customers
        customer_keys = {tuple(row[field] for field in CUSTOMER_COLUMNS) for row in customers}
        summaries.append({'account': name, 'customers_source': len(customers), 'customers_unique': len(customer_keys),
                          'customers_missing_values': sum(not all(row[field] for field in CUSTOMER_COLUMNS) for row in customers),
                          'customer_files_differ': different, 'orders': len(orders),
                          'invalid_order_dates': sum(order_day(row['date']) is None for row in orders)})
        datasets.append((account, customers, orders))
    return source, datasets, products, manifest, {'accounts': summaries, 'products': len(price_map), 'files': manifest}


def migrate(database, directory, apply=False):
    source, datasets, products, manifest, report = inspect_source(directory)
    if not apply:
        return dict(report, status='dry-run')
    signature = payload_hash(manifest)
    with database.session() as session:
        previous = session.scalar(select(MigrationRun).where(MigrationRun.source_path == str(source)))
        if previous:
            if previous.manifest_hash != signature:
                raise ValueError('Previously imported source changed. Stop and review; automatic re-import is prohibited.')
            return dict(json.loads(previous.report_json), status='already-imported')
        if session.scalar(select(Account.id).limit(1)) is not None:
            raise ValueError('Initial migration requires an empty database. Existing data was not changed.')
    backup_dir = database.directory.parent / (database.directory.name + '-source-backup') / signature[:16]
    backup_dir.mkdir(parents=True, exist_ok=True)
    for filename in manifest:
        shutil.copy2(source / filename, backup_dir / filename)
        if digest((backup_dir / filename).read_bytes()) != manifest[filename]:
            raise ValueError('Source changed while backing up; migration aborted')
    database.backup('before-migration')
    with database.session(write=True) as session:
        if session.scalar(select(Account.id).limit(1)) is not None:
            raise ValueError('Another migration created accounts; migration aborted')
        product_ids = {}
        for row in products:
            if row['item'] in product_ids:
                continue
            product = Product(item=row['item'], price=row['price'])
            session.add(product)
            session.flush()
            product_ids[row['item']] = product.id
        for values, customers, orders in datasets:
            account = Account(name=values['name'], password_hash=generate_password_hash(values['pw']))
            session.add(account)
            session.flush()
            customer_ids = {}
            for row in customers:
                key = tuple(row[field] for field in CUSTOMER_COLUMNS)
                if key in customer_ids:
                    continue
                customer = Customer(account_id=account.id, **{field: row[field] for field in CUSTOMER_COLUMNS})
                session.add(customer)
                session.flush()
                customer_ids[key] = customer.id
            for row in orders:
                values = {field: row.get(field, '') for field in ORDER_COLUMNS}
                session.add(Order(account_id=account.id, **values, order_day=order_day(values['date']),
                                  customer_id=customer_ids.get(tuple(row[field] for field in CUSTOMER_COLUMNS)),
                                  product_id=product_ids.get(row['item'])))
        report['status'] = 'imported'
        session.add(MigrationRun(source_path=str(source), manifest_hash=signature, report_json=json.dumps(report)))
    return report
