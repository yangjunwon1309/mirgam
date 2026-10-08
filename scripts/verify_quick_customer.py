"""Verify order-page registration using temporary customer/order files."""
import csv
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.app import app


class QuickCustomerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.customer_path = self.root / 'customers.csv'
        self.customer_path.write_text('name,ph,address\nExisting,010-1111,Address A\n', encoding='utf-8-sig')
        self.order_path = self.root / 'orders.csv'
        self.order_path.write_text('name,ph,address,item,quantity,date\n', encoding='utf-8-sig')
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session['customer_upload_path'] = str(self.customer_path)
            session['order_path'] = str(self.order_path)

    def create(self, **values):
        return self.client.post('/customers/new', json=values or {
            'new_name': 'New Customer', 'new_phone': '010-2222', 'new_address': 'Address B',
        })

    def rows(self):
        with self.customer_path.open(encoding='utf-8-sig', newline='') as file:
            return list(csv.DictReader(file))

    def test_registration_is_searchable_and_visible_in_customer_management(self):
        original_orders = self.order_path.read_bytes()
        response = self.create()
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.get_json()['created'])
        self.assertEqual(len(self.rows()), 2)
        self.assertEqual(self.client.get('/customers/search?q=New').get_json(), [response.get_json()['customer']])
        self.assertIn('New Customer', self.client.get('/customer').get_data(as_text=True))
        self.assertEqual(self.order_path.read_bytes(), original_orders)

    def test_exact_duplicate_returns_existing_customer(self):
        response = self.create(new_name='Existing', new_phone='010-1111', new_address='Address A')
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()['created'])
        self.assertEqual(len(self.rows()), 1)

    def test_different_address_remains_a_distinct_customer(self):
        response = self.create(new_name='Existing', new_phone='010-1111', new_address='Address B')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(len(self.rows()), 2)

    def test_invalid_fields_never_change_csv(self):
        original = self.customer_path.read_bytes()
        for values in [{'new_name': 'Missing'}, {'new_name': ' ', 'new_phone': '010', 'new_address': 'A'},
                       {'new_name': ['Wrong type'], 'new_phone': '010', 'new_address': 'A'}]:
            self.assertEqual(self.create(**values).status_code, 400)
        self.assertEqual(self.customer_path.read_bytes(), original)

    def test_customer_management_uses_same_duplicate_rule(self):
        self.client.post('/customer', data={'new_name': 'Existing', 'new_phone': '010-1111', 'new_address': 'Address A'})
        self.assertEqual(len(self.rows()), 1)

    def test_reordered_columns_keep_values_in_correct_columns(self):
        self.customer_path.write_text('ph,address,name\n010-1111,Address A,Existing\n', encoding='utf-8-sig')
        response = self.create()
        self.assertEqual(self.rows()[-1], response.get_json()['customer'])

    def test_registration_requires_session(self):
        self.assertEqual(app.test_client().post('/customers/new', json={}).status_code, 401)


if __name__ == '__main__':
    unittest.main()
