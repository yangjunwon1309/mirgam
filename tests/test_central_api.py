"""Isolated acceptance tests: fixtures only, never the user's source CSVs."""
from concurrent.futures import ThreadPoolExecutor
import csv
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from openpyxl import Workbook
from sqlalchemy import select
from werkzeug.serving import make_server

from apps.app import create_app as create_client
from server.app import create_app
from server.common import ORDER_COLUMNS, csv_bytes
from server.migrate import migrate
from server.models import Account, Customer, Order


def fixture(directory):
    directory.mkdir()
    (directory / 'login.csv').write_text('name,pw\nFarm A,001234\nFarm B,4321\n', encoding='utf-8-sig')
    (directory / 'items.csv').write_text('item,price\nProduct,100\nOther,\n', encoding='utf-8-sig')
    for name in ('Farm A', 'Farm B'):
        rows = [{'name': 'Same', 'ph': '010-1111', 'address': 'Address A'},
                {'name': 'Same', 'ph': '010-1111', 'address': 'Address B'}]
        (directory / f'customer_upload_{name}.csv').write_bytes(csv_bytes(rows, ['name', 'ph', 'address']))
        (directory / f'customer_{name}.csv').write_bytes(csv_bytes(rows, ['name', 'ph', 'address']))
        order = dict(name='Same', ph='010-1111', address='Address A', item='Product', quantity='2', date='2026-10-07',
                     sender_name='Sender', sender_ph='010-9999', sender_address='Sender address')
        (directory / f'order_{name}.csv').write_bytes(csv_bytes([order, order], ORDER_COLUMNS))


class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        fixture(self.source)
        self.app = create_app(self.root / 'server', testing=True)
        self.database = self.app.extensions['database']
        self.addCleanup(self.database.engine.dispose)
        migrate(self.database, self.source, apply=True)
        self.client = self.app.test_client()
        self.headers = self.login('Farm A', '001234')
        self.other_headers = self.login('Farm B', '4321')

    def login(self, name, password):
        response = self.client.post('/api/v1/auth/login', json={'name': name, 'password': password})
        self.assertEqual(response.status_code, 200, response.get_json())
        return {'Authorization': 'Bearer ' + response.get_json()['data']['token']}

    def get(self, path, **kwargs):
        return self.client.get('/api/v1' + path, headers=self.headers, **kwargs)

    def post(self, path, **kwargs):
        return self.client.post('/api/v1' + path, headers=self.headers, **kwargs)

    def test_migration_repeat_and_original_strings(self):
        self.assertEqual(migrate(self.database, self.source, True)['status'], 'already-imported')
        rows = self.get('/orders').get_json()['data']
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['ph'], '010-1111')
        self.assertEqual(rows[0]['sender_address'], 'Sender address')
        with self.database.session() as session:
            self.assertNotEqual(session.scalar(select(Account.password_hash)), '001234')
        (self.source / 'items.csv').write_text('item,price\nChanged,0\n', encoding='utf-8-sig')
        with self.assertRaises(ValueError):
            migrate(self.database, self.source, True)

    def test_auth_and_static_data_protection(self):
        for path in ('/api/v1/customers', '/static/login.csv', '/static/order.csv'):
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.post('/api/v1/auth/login', json={'name': 'Farm A', 'password': '1234'}).status_code, 401)
        self.assertEqual(self.client.get('/static/login.csv', headers=self.headers).status_code, 404)

    def test_customers_distinct_duplicate_and_quantity_sort(self):
        customers = self.get('/customers?sort=quantity').get_json()['data']
        self.assertEqual([row['order_quantity'] for row in customers], [4, 0])
        response = self.post('/customers', json={'name': 'Same', 'ph': '010-1111', 'address': 'Address A'})
        self.assertFalse(response.get_json()['data']['created'])
        self.assertEqual(response.get_json()['data']['customer']['id'], customers[0]['id'])
        deleted = self.client.delete('/api/v1/customers/' + str(customers[0]['id']), headers=self.headers)
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(self.get('/customers').get_json()['total'], 1)
        self.assertEqual(self.get('/orders').get_json()['total'], 2)
        reactivated = self.post('/customers', json={'name': 'Same', 'ph': '010-1111', 'address': 'Address A'})
        self.assertEqual(reactivated.get_json()['data']['customer']['id'], customers[0]['id'])

    def test_customer_edit_keeps_snapshot_and_account_boundary(self):
        customer = self.get('/customers').get_json()['data'][0]
        url = '/api/v1/customers/' + str(customer['id'])
        self.assertEqual(self.client.patch(url, headers=self.other_headers, json={'name': 'Changed', 'ph': '010', 'address': 'New'}).status_code, 404)
        self.assertEqual(self.client.patch(url, headers=self.headers, json={'name': 'Changed', 'ph': '010', 'address': 'New'}).status_code, 200)
        self.assertEqual(self.get('/orders').get_json()['data'][0]['address'], 'Address A')

    def test_concurrent_duplicate_registration(self):
        def create(_):
            with self.app.test_client() as client:
                return client.post('/api/v1/customers', headers=self.headers,
                    json={'name': 'Concurrent', 'ph': '010-2222', 'address': 'C'}).get_json()['data']['customer']['id']
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, range(2)))
        self.assertEqual(results[0], results[1])

    def batch(self, request_id='batch-1', quantity='3'):
        customer = self.get('/customers').get_json()['data'][0]
        product = self.get('/products').get_json()['data'][0]
        return {'request_id': request_id, 'orders': [{'customer_id': customer['id'], 'product_id': product['id'],
                  'quantity': quantity, 'date': '2026-10-07', 'sender_name': 'Sender'}]}

    def test_idempotent_batches_and_one_order_delete(self):
        payload = self.batch()
        first = self.post('/order-batches', json=payload)
        self.assertEqual(first.status_code, 201, first.get_json())
        replay = self.post('/order-batches', json=payload)
        self.assertEqual(first.get_json(), replay.get_json())
        self.assertEqual(self.get('/orders').get_json()['total'], 3)
        payload['orders'][0]['quantity'] = '10'
        self.assertEqual(self.post('/order-batches', json=payload).status_code, 409)
        rows = self.get('/orders').get_json()['data']
        self.client.delete('/api/v1/orders/' + str(rows[0]['id']), headers=self.headers)
        self.assertEqual(self.get('/orders').get_json()['total'], 2)

    def test_invalid_batch_is_atomic(self):
        for value in ('NaN', 'Infinity', '0', '-1', 'bad', '1e9999'):
            self.assertEqual(self.post('/order-batches', json=self.batch(quantity=value)).status_code, 422)
        payload = self.batch()
        payload['orders'].append(dict(payload['orders'][0], date='2026-02-30'))
        self.assertEqual(self.post('/order-batches', json=payload).status_code, 422)
        self.assertEqual(self.get('/orders').get_json()['total'], 2)

    def test_export_uses_same_date_name_filters_not_page(self):
        result = self.get('/orders?date=2026-10-07&q=Same&page_size=1').get_json()
        self.assertEqual(result['total'], 2)
        response = self.get('/orders/export.csv?date=2026-10-07&q=Same&page_size=1')
        rows = list(csv.DictReader(io.StringIO(response.data.decode('utf-8-sig'))))
        self.assertEqual(len(rows), result['total'])
        self.assertEqual(list(rows[0]), ORDER_COLUMNS)
        self.assertEqual(self.get('/orders?q=%').get_json()['total'], 0)

    def upload(self, content=b'Title,Phone,Address\nNew,010-3333,New Address\n', filename='customers.csv'):
        result = self.post('/customer-imports', data={'file': (io.BytesIO(content), filename)})
        self.assertEqual(result.status_code, 201, result.get_json())
        return result.get_json()['data']['id']

    def preview(self, identifier, **settings):
        return self.post('/customer-imports/' + identifier + '/preview', json=dict(
            header_row=0, mapping={'name': 0, 'ph': 1, 'address': 2}, **settings))

    def apply(self, identifier, preview):
        return self.post('/customer-imports/' + identifier + '/apply', json={
            'version': preview['version'], 'confirm_add': True})

    def test_preview_is_read_only_and_add_preserves_existing_customers_and_orders(self):
        identifier = self.upload()
        self.assertEqual(self.get('/customers').get_json()['total'], 2)
        preview = self.preview(identifier).get_json()['data']
        self.assertEqual(preview['counts']['new'], 1)
        self.assertEqual(preview['counts']['kept'], 0)
        self.assertEqual(self.get('/customers').get_json()['total'], 2)
        applied = self.apply(identifier, preview)
        self.assertEqual(applied.status_code, 200, applied.get_json())
        self.assertEqual(self.apply(identifier, preview).get_json(), applied.get_json())
        self.assertEqual(self.get('/customers').get_json()['total'], 3)
        self.assertEqual(self.get('/orders').get_json()['total'], 2)

    def test_preview_conflict_and_invalid_mapping_do_not_change_customers(self):
        identifier = self.upload()
        preview = self.preview(identifier).get_json()['data']
        self.post('/customers', json={'name': 'Other', 'ph': '010', 'address': 'Changed'})
        self.assertEqual(self.apply(identifier, preview).status_code, 409)
        self.assertEqual(self.get('/customers').get_json()['total'], 3)
        response = self.post('/customer-imports/' + identifier + '/preview', json={'mapping': {'name': 0, 'ph': 0, 'address': 2}})
        self.assertEqual(response.status_code, 422)

    def test_invalid_file_empty_rows_cancel_and_ownership(self):
        identifier = self.upload(b'A,B,C\nBad,,Address\n')
        preview = self.preview(identifier).get_json()['data']
        self.assertFalse(preview['can_apply'])
        self.assertEqual(self.apply(identifier, preview).status_code, 422)
        self.assertEqual(self.client.delete('/api/v1/customer-imports/' + identifier, headers=self.other_headers).status_code, 404)
        self.client.delete('/api/v1/customer-imports/' + identifier, headers=self.headers)
        self.assertEqual(self.get('/customers').get_json()['total'], 2)
        self.assertEqual(self.preview(identifier).status_code, 409)
        empty = self.upload(b'A,B,C\n')
        self.assertFalse(self.preview(empty).get_json()['data']['can_apply'])

    def test_xlsx_sheet_mapping_duplicate_headers_and_numeric_phone(self):
        workbook = Workbook()
        workbook.active.title = 'Wrong'
        sheet = workbook.create_sheet('Customers')
        sheet.append(['Title', 'Title', 'Address'])
        sheet.append(['Excel customer', '010-5555', 'E'])
        content = io.BytesIO()
        workbook.save(content)
        identifier = self.upload(content.getvalue(), 'customers.xlsx')
        preview = self.preview(identifier, sheet='Customers').get_json()['data']
        self.assertTrue(preview['can_apply'])
        self.assertEqual(preview['rows'][0]['ph'], '010-5555')
        sheet['B2'] = 1055555555
        content = io.BytesIO()
        workbook.save(content)
        numeric = self.upload(content.getvalue(), 'numeric.xlsx')
        self.assertFalse(self.preview(numeric, sheet='Customers').get_json()['data']['can_apply'])

    def test_csv_cp949_no_header(self):
        value = '\ud64d\uae38\ub3d9,010-7777,\uc11c\uc6b8\n'.encode('cp949')
        identifier = self.upload(value)
        response = self.post('/customer-imports/' + identifier + '/preview', json={
            'header_row': None, 'encoding': 'cp949', 'mapping': {'name': 0, 'ph': 1, 'address': 2}})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['data']['can_apply'])

    def test_backup_integrity_and_foreign_keys(self):
        backup = self.database.backup('test')
        with sqlite3.connect(backup) as connection:
            self.assertEqual(connection.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
            self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM orders').fetchone()[0], 4)


class ClientTests(APITests):
    # Separate live HTTP central API; desktop UI itself uses a Flask test client.
    # Inherited API scenarios also run through this live server setup.
    def setUp(self):
        super().setUp()
        self.http_server = make_server('127.0.0.1', 0, self.app, threaded=True)
        self.thread = threading.Thread(target=self.http_server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.ui = create_client('http://127.0.0.1:' + str(self.http_server.server_port), testing=True)
        self.desktop = self.ui.test_client()
        self.ui_login(self.desktop)
        self.ui.config['ORDER_SAVE_DIALOG'] = lambda directory, filename: str(self.root / 'export.csv')

    def stop_server(self):
        self.http_server.shutdown()
        self.thread.join(timeout=5)
        self.http_server.server_close()

    def csrf(self, client=None):
        with (client or self.desktop).session_transaction() as session:
            return session['csrf']

    def ui_login(self, client):
        client.get('/login')
        result = client.post('/login', data={'farm-name': 'Farm A', 'password': '001234', 'csrf_token': self.csrf(client)})
        self.assertEqual(result.status_code, 302)
        self.assertEqual(client.get('/order').status_code, 200)

    def draft(self):
        customer = self.get('/customers').get_json()['data'][0]
        return {'customer_id': customer['id'], 'name': customer['name'], 'phone': customer['ph'], 'address': customer['address'],
                'item': 'Product', 'quantity': '3', 'date': '2026-10-07'}

    def save(self, identifier='0' * 32):
        return self.desktop.post('/order', data={'orders': json.dumps([self.draft()]), 'request_id': identifier,
                                                'csrf_token': self.csrf()})

    def test_ui_pages_cookie_and_csrf(self):
        for path in ('/order', '/order_view', '/customer', '/mypage', '/contact'):
            self.assertEqual(self.desktop.get(path).status_code, 200, path)
        order_html = self.desktop.get('/order').get_data(as_text=True)
        self.assertLess(order_html.index('id="selection-count"'), order_html.index('id="customer_name_input"'))
        self.assertIn('data-remove-order', order_html)
        customer_html = self.desktop.get('/customer').get_data(as_text=True)
        self.assertIn('customer-actions-dialog', customer_html)
        self.assertIn('action-orders', customer_html)
        self.assertIn('customer-row', customer_html)
        self.assertEqual(self.desktop.post('/customers/new', json={}).status_code, 403)
        with self.desktop.session_transaction() as session:
            self.assertNotIn('token', session)
            self.assertNotIn('order_path', session)
        self.assertEqual(self.desktop.get('/static/login.csv').status_code, 404)

    def test_two_independent_clients_share_new_customer(self):
        second = create_client(self.ui.extensions['api_client'].base_url, testing=True).test_client()
        self.ui_login(second)
        response = self.desktop.post('/customers/new', json={'new_name': 'Shared', 'new_phone': '010-0000', 'new_address': 'New'},
                                     headers={'X-CSRF-Token': self.csrf()})
        self.assertEqual(response.status_code, 201)
        found = second.get('/customers/search?q=Shared').get_json()
        self.assertEqual(found[0]['id'], response.get_json()['customer']['id'])

    def test_order_save_and_replay_do_not_append_csv_sources(self):
        original = (self.source / 'order_Farm A.csv').read_bytes()
        self.assertEqual(self.save().status_code, 302)
        self.assertEqual(self.get('/orders').get_json()['total'], 3)
        self.assertEqual(self.save().status_code, 302)
        self.assertEqual(self.get('/orders').get_json()['total'], 3)
        self.assertEqual((self.source / 'order_Farm A.csv').read_bytes(), original)
        self.assertTrue((self.root / 'export.csv').read_bytes().startswith(b'\xef\xbb\xbf'))

    def test_cancel_save_keeps_draft_no_database_write(self):
        self.ui.config['ORDER_SAVE_DIALOG'] = lambda *args: ''
        response = self.save()
        self.assertEqual(response.status_code, 200)
        self.assertIn('DRAFT_ORDERS', response.get_data(as_text=True))
        self.assertEqual(self.get('/orders').get_json()['total'], 2)

    def test_committed_export_failure_retry_only_saves_csv(self):
        original = self.ui.extensions['api_client'].transport
        def fail_export(method, url, **kwargs):
            if '/order-batches/' in url and url.endswith('/export.csv'):
                import requests
                raise requests.ConnectionError('test failure')
            return original(method, url, **kwargs)
        self.ui.extensions['api_client'].transport = fail_export
        response = self.save()
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-committed="true"', response.get_data(as_text=True))
        self.assertEqual(self.get('/orders').get_json()['total'], 3)
        self.ui.extensions['api_client'].transport = original
        self.assertEqual(self.save().status_code, 302)
        self.assertEqual(self.get('/orders').get_json()['total'], 3)

    def test_lost_save_response_retry_is_idempotent(self):
        original = self.ui.extensions['api_client'].transport
        def lose_response(method, url, **kwargs):
            response = original(method, url, **kwargs)
            if method == 'POST' and url.endswith('/order-batches'):
                import requests
                raise requests.ConnectionError('response lost after commit')
            return response
        self.ui.extensions['api_client'].transport = lose_response
        response = self.save()
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-uncertain="true"', response.get_data(as_text=True))
        self.assertEqual(self.get('/orders').get_json()['total'], 3)
        self.ui.extensions['api_client'].transport = original
        self.assertEqual(self.save().status_code, 302)
        self.assertEqual(self.get('/orders').get_json()['total'], 3)

    def test_bad_local_path_does_not_save_database(self):
        self.ui.config['ORDER_SAVE_DIALOG'] = lambda *args: str(self.root / 'missing' / 'orders.csv')
        self.assertEqual(self.save().status_code, 200)
        self.assertEqual(self.get('/orders').get_json()['total'], 2)

    def test_client_customer_and_product_edit_and_one_delete(self):
        customer = self.get('/customers').get_json()['data'][0]
        response = self.desktop.post('/customer', data={'csrf_token': self.csrf(), 'action': 'edit',
            'customer_id': customer['id'], 'new_name': 'Edited', 'new_phone': customer['ph'], 'new_address': customer['address']})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.get('/orders').get_json()['data'][0]['name'], 'Same')
        self.desktop.post('/customer', data={'csrf_token': self.csrf(), 'customer_id': customer['id']})
        self.assertEqual(self.get('/customers').get_json()['total'], 3)
        self.assertEqual(self.get('/orders').get_json()['total'], 2)
        product = self.get('/products').get_json()['data'][0]
        response = self.desktop.post('/mypage', data={'csrf_token': self.csrf(), 'product_id': product['id'], 'action': 'edit', 'price': '200'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.get('/products').get_json()['data'][0]['price'], '200')

    def test_ui_import_preview_and_confirm(self):
        response = self.desktop.post('/upload', data={'csrf_token': self.csrf(), 'file': (io.BytesIO(b'A,B,C\nNew,010-2222,New Address\n'), 'new.csv')})
        self.assertEqual(response.status_code, 200)
        self.assertIn('map-name', response.get_data(as_text=True))
        with self.database.session() as session:
            from server.models import CustomerImport
            identifier = session.scalar(select(CustomerImport.id))
        response = self.desktop.post('/upload/preview/' + identifier, data={'csrf_token': self.csrf(), 'name': '0', 'ph': '1', 'address': '2', 'header_row': '0'})
        self.assertEqual(response.status_code, 200)
        self.assertIn('confirm_add', response.get_data(as_text=True))
        with self.database.session() as session:
            version = session.get(CustomerImport, identifier).preview_version
        response = self.desktop.post('/upload/apply/' + identifier, data={'csrf_token': self.csrf(), 'version': version, 'confirm_add': 'yes'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.get('/customers').get_json()['total'], 3)
        self.assertEqual(self.get('/orders').get_json()['total'], 2)


if __name__ == '__main__':
    unittest.main()
