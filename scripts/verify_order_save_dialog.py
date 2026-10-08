"""Check Save As defaults, cancellation, export errors and order history."""
import csv
import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.app import app, ORDER_COLUMNS
from apps import order_save_dialog


class OrderSaveTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.history = self.root / 'order.csv'
        self.history.write_text(','.join(ORDER_COLUMNS) + '\n', encoding='utf-8')
        self.items = self.root / 'items.csv'
        self.items.write_text('item,price\nProduct,\n', encoding='utf-8')
        self.customer = dict(name='Customer', phone='010-1234-5678', address='Address',
                             item='Product', quantity='2', date='2026-10-06',
                             sender_name='Sender', sender_ph='010-1111-1111', sender_address='Sender address')
        self.client = app.test_client()
        with self.client.session_transaction() as session:
            session['order_path'] = str(self.history)
            session['items_path'] = str(self.items)
        self.defaults = patch.dict(app.config, {'ORDER_EXPORT_DIRECTORY': str(self.root)})
        self.defaults.start()
        self.addCleanup(self.defaults.stop)

    def submit(self, picker):
        with patch.dict(app.config, {'ORDER_SAVE_DIALOG': picker}):
            return self.client.post('/order', data={'orders': json.dumps([self.customer])})

    @staticmethod
    def read_csv(path):
        with path.open(encoding='utf-8-sig', newline='') as file:
            return list(csv.DictReader(file))

    def test_defaults_and_custom_filename(self):
        selected = self.root / 'selected folder' / 'custom order.csv'
        picker = Mock(return_value=str(selected))
        response = self.submit(picker)
        picker.assert_called_once_with(str(self.root), datetime.date.today().strftime('%Y_%m_%d') + '.csv')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.read_csv(self.history), self.read_csv(selected))
        self.assertEqual(len(self.read_csv(selected)), 1)

    def test_cancel_preserves_draft_without_writing(self):
        original = self.history.read_bytes()
        response = self.submit(lambda directory, name: '')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.history.read_bytes(), original)
        self.assertIn('const DRAFT_ORDERS=[', response.get_data(as_text=True))
        self.assertIn('Sender address', response.get_data(as_text=True))
        self.assertEqual(sorted(file.name for file in self.root.iterdir()), ['items.csv', 'order.csv'])

    def test_failed_export_does_not_append_history(self):
        original = self.history.read_bytes()
        with patch('apps.app.os.replace', side_effect=PermissionError('export file is locked')):
            response = self.submit(lambda directory, name: str(self.root / name))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.history.read_bytes(), original)
        self.assertEqual(sorted(file.name for file in self.root.iterdir()), ['items.csv', 'order.csv'])

    def test_chosen_existing_export_is_replaced_history_is_appended(self):
        selected = self.root / 'existing.csv'
        selected.write_text('Old export', encoding='utf-8')
        picker = lambda directory, name: str(selected)
        self.submit(picker)
        self.submit(picker)
        self.assertEqual(len(self.read_csv(selected)), 1)
        self.assertEqual(len(self.read_csv(self.history)), 2)

    def test_account_history_cannot_be_overwritten(self):
        original = self.history.read_bytes()
        response = self.submit(lambda directory, name: str(self.history))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.history.read_bytes(), original)

    def test_dialog_runs_in_separate_process(self):
        result = Mock(stdout=json.dumps('C:/chosen/orders.csv'))
        with patch.object(order_save_dialog.subprocess, 'run', return_value=result) as run:
            self.assertEqual(order_save_dialog.choose_order_export_path('C:/Downloads', '2026_10_06.csv'), 'C:/chosen/orders.csv')
            command = run.call_args.args[0]
            self.assertEqual(command[-2:], ['C:/Downloads', '2026_10_06.csv'])


if __name__ == '__main__':
    unittest.main()
