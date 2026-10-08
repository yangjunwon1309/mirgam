import csv
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json

CUSTOMER_COLUMNS = ['name', 'ph', 'address']
ORDER_COLUMNS = CUSTOMER_COLUMNS + ['item', 'quantity', 'date', 'sender_name', 'sender_ph', 'sender_address']


class APIError(Exception):
    def __init__(self, message, status=422, code='invalid_input', details=None):
        self.message, self.status, self.code, self.details = message, status, code, details


def digest(value):
    return hashlib.sha256(value).hexdigest()


def payload_hash(value):
    return digest(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode())


def positive_quantity(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and 0 < number <= Decimal('1000000000000') else None
    except InvalidOperation:
        return None


def order_day(value):
    candidate = str(value)[:10]
    try:
        return candidate if date.fromisoformat(candidate).isoformat() == candidate else None
    except ValueError:
        return None


def record(obj, columns):
    return {column: getattr(obj, column) for column in columns}


def csv_bytes(rows, columns):
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode('utf-8-sig')
