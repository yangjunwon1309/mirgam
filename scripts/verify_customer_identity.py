"""Regression checks for address-specific customer actions using temporary CSVs."""
import csv
import sys
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.app import app, customer_order_quantity_map, customer_key, ORDER_COLUMNS


class CustomerDeleteForms(HTMLParser):
    def __init__(self):
        super().__init__()
        self.forms = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self.current = {} if attrs.get('action') == '/customer' else None
        elif tag == 'input' and self.current is not None:
            self.current[attrs['name']] = attrs.get('value', '')

    def handle_endtag(self, tag):
        if tag == 'form' and self.current is not None:
            self.forms.append(self.current)
            self.current = None


class CustomerIdentityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.customer_path = Path(self.directory.name) / 'customers.csv'
        self.order_path = Path(self.directory.name) / 'orders.csv'
        self.customers = [
            {'name': 'Same customer', 'ph': '01012345678', 'address': address}
            for address in ['Address A', 'Address "B"']
        ]
        self.write_csv(self.customer_path, ['name', 'ph', 'address'], self.customers)
        self.orders = [
            dict(customer, item='Product', quantity=str(quantity), date='2026-10-06',
                 sender_name='', sender_ph='', sender_address='')
            for customer, quantity in [(self.customers[0], 2), (self.customers[0], 3), (self.customers[1], 7)]
        ]
        self.write_csv(self.order_path, ORDER_COLUMNS, self.orders)
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session['customer_upload_path'] = str(self.customer_path)
            session['order_path'] = str(self.order_path)

    @staticmethod
    def write_csv(path, columns, rows):
        with path.open('w', newline='', encoding='utf-8-sig') as file:
            writer = csv.DictWriter(file, fieldnames=columns)
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def read_csv(path):
        with path.open(encoding='utf-8-sig', newline='') as file:
            return list(csv.DictReader(file))

    def test_quantities_are_separate_by_address(self):
        quantities = customer_order_quantity_map(self.order_path)
        self.assertEqual(quantities[customer_key(self.customers[0])], 5)
        self.assertEqual(quantities[customer_key(self.customers[1])], 7)
        response = self.client.get('/customer?sort=quantity')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertLess(html.index('value="Address &#34;B&#34;"'), html.index('value="Address A"'))

    def test_search_preserves_phone_and_both_addresses(self):
        results = self.client.get('/customers/search?q=Same').get_json()
        self.assertEqual(results, self.customers)

    def test_delete_only_matching_customer_address(self):
        forms = CustomerDeleteForms()
        forms.feed(self.client.get('/customer').get_data(as_text=True))
        target = next(form for form in forms.forms if form['selected_address'] == 'Address A')
        self.client.post('/customer', data=target)
        self.assertEqual(self.read_csv(self.customer_path), [self.customers[1]])

    def test_exact_duplicates_display_once(self):
        self.write_csv(self.customer_path, ['name', 'ph', 'address'], self.customers + [self.customers[0]])
        self.assertEqual(self.client.get('/customers/search?q=Same').get_json(), self.customers)
        forms = CustomerDeleteForms()
        forms.feed(self.client.get('/customer').get_data(as_text=True))
        self.assertEqual(len(forms.forms), 2)

    def test_incomplete_delete_never_removes_any_customers(self):
        self.client.post('/customer', data={'selected_name': 'Same customer', 'selected_ph': '01012345678'})
        self.assertEqual(self.read_csv(self.customer_path), self.customers)

    def test_order_deletion_preserves_other_address(self):
        self.client.post('/order_view', data={
            'selected_name': 'Same customer', 'selected_ph': '01012345678',
            'selected_address': 'Address A', 'selected_date': '2026-10-06',
        })
        self.assertEqual(self.read_csv(self.order_path), [self.orders[2]])

    def test_empty_customer_list(self):
        self.write_csv(self.customer_path, ['name', 'ph', 'address'], [])
        self.assertEqual(self.client.get('/customer').status_code, 200)
        self.assertEqual(self.client.get('/customers/search?q=Same').get_json(), [])


if __name__ == '__main__':
    unittest.main()
