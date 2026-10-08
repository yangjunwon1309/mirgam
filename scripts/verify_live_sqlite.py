"""Read-only HTTP verification of the migrated farm, using credentials locally.

Credentials, tokens and customer values are never printed. Business records are
not modified; an authentication session is created and revoked for verification.
"""
import argparse
from collections import Counter
import csv
import io
from pathlib import Path
import re
import sys

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server.common import CUSTOMER_COLUMNS, ORDER_COLUMNS, digest
from server.migrate import inspect_source
from server.db import Database
from server.models import MigrationRun
from sqlalchemy import select


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='apps/static')
    parser.add_argument('--data-dir', default='var/server')
    parser.add_argument('--api-url', default='http://127.0.0.1:5100')
    parser.add_argument('--client-url', default='http://127.0.0.1:5000')
    parser.add_argument('--account', default='준이네 농장')
    args = parser.parse_args()
    source, datasets, products, manifest, _ = inspect_source(args.source)
    account, customer_rows, order_rows = next(row for row in datasets if row[0]['name'] == args.account)
    database = Database(args.data_dir, initialize=False)
    try:
        with database.session() as session:
            run = session.scalar(select(MigrationRun).where(MigrationRun.source_path == str(source)))
            import json
            assert run and json.loads(run.report_json)['files'] == manifest, 'Source CSV hashes changed'
    finally:
        database.engine.dispose()
    print('PASS: source CSV SHA-256 values unchanged since migration')
    with requests.Session() as api:
        response = api.post(args.api_url + '/api/v1/auth/login', json={'name': account['name'], 'password': account['pw']}, timeout=30)
        response.raise_for_status()
        api.headers['Authorization'] = 'Bearer ' + response.json()['data']['token']
        try:
            for endpoint, expected in (('customers', len({tuple(row[field] for field in CUSTOMER_COLUMNS) for row in customer_rows})), ('orders', len(order_rows))):
                response = api.get(args.api_url + '/api/v1/' + endpoint, timeout=30)
                response.raise_for_status()
                assert response.json()['total'] == expected
                print(f'PASS: {endpoint} count = {expected}')
            for endpoint, columns, expected in (
                ('customers', CUSTOMER_COLUMNS, set(tuple(row[field] for field in CUSTOMER_COLUMNS) for row in customer_rows)),
                ('orders', ORDER_COLUMNS, Counter(tuple(row.get(field, '') for field in ORDER_COLUMNS) for row in order_rows)),
            ):
                response = api.get(args.api_url + '/api/v1/' + endpoint + '/export.csv', timeout=30)
                response.raise_for_status()
                rows = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
                actual = [tuple(row[field] for field in columns) for row in rows]
                assert (set(actual) if endpoint == 'customers' else Counter(actual)) == expected
                print(f'PASS: every {endpoint} field preserved, including duplicate order rows')
            response = api.get(args.api_url + '/api/v1/products', timeout=30)
            response.raise_for_status()
            assert {(row['item'], row['price']) for row in response.json()['data']} == {(row['item'], row['price']) for row in products}
            print(f'PASS: {len(response.json()["data"])} products and original price strings preserved')
            day = next(row['date'][:10] for row in order_rows if re.fullmatch(r'\d{4}-\d{2}-\d{2}', row['date'][:10]))
            listed = api.get(args.api_url + '/api/v1/orders', params={'date': day, 'page_size': 1}, timeout=30).json()
            exported = api.get(args.api_url + '/api/v1/orders/export.csv', params={'date': day}, timeout=30)
            exported.raise_for_status()
            rows = list(csv.DictReader(io.StringIO(exported.content.decode('utf-8-sig'))))
            assert len(rows) == listed['total'] and list(rows[0]) == ORDER_COLUMNS
            assert all(row['date'][:10] == day for row in rows)
            print('PASS: filtered export includes all matching rows and the original nine columns')
        finally:
            api.post(args.api_url + '/api/v1/auth/logout', json={}, timeout=30)
    with requests.Session() as client:
        page = client.get(args.client_url + '/login', timeout=30)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        response = client.post(args.client_url + '/login', data={'farm-name': account['name'], 'password': account['pw'], 'csrf_token': token}, timeout=30)
        response.raise_for_status()
        assert '/order' in response.url, 'Login did not reach orders'
        for endpoint in ('order', 'order_view', 'customer', 'mypage', 'contact'):
            response = client.get(args.client_url + '/' + endpoint, timeout=30)
            assert response.status_code == 200
            print(f'PASS: /{endpoint} HTTP 200')
        token = re.search(r'name="csrf_token" value="([^"]+)"', response.text).group(1)
        client.post(args.client_url + '/logout', data={'csrf_token': token}, timeout=30)
    assert all(digest((source / filename).read_bytes()) == value for filename, value in manifest.items())
    print('PASS: source CSVs still unchanged after live verification')


if __name__ == '__main__':
    main()
