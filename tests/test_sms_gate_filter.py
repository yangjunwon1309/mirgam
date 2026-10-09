import unittest
from types import SimpleNamespace

from server.sms_gate import has_order_intent, maybe_order_message


class SmsOrderFilterTests(unittest.TestCase):
    def setUp(self):
        self.orange = SimpleNamespace(item='귤')

    def test_registered_product_name_alone_is_a_review_candidate(self):
        product, quantity = maybe_order_message('귤 1개', [self.orange])
        self.assertIs(product, self.orange)
        self.assertEqual(quantity, '1')

    def test_order_terms_allow_spacing_and_common_typos(self):
        self.assertTrue(has_order_intent('보내 주 세 요'))
        self.assertTrue(has_order_intent('귤 한 상자 보네주세요'))
        self.assertTrue(has_order_intent('신청할께요'))

    def test_unrelated_text_without_product_or_order_term_is_ignored(self):
        self.assertEqual(maybe_order_message('오늘 날씨가 좋네요', [self.orange]), (None, ''))

    def test_empty_or_overlong_messages_are_ignored_even_if_product_matches(self):
        self.assertEqual(maybe_order_message('', [self.orange]), (None, ''))
        self.assertEqual(maybe_order_message('귤' * 5001, [self.orange]), (None, ''))


if __name__ == '__main__':
    unittest.main()
